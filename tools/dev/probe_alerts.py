# -*- coding: utf-8 -*-
"""告警规则自检：把 10 条规则逐条造出来，看 evaluate() 是否命中、apply() 是否落库、恢复是否置 resolved。

只在**网关容器内**跑（要访问 MySQL / Redis / cgroup；宿主机没有这些依赖）：

    docker cp tools/dev/probe_alerts.py kk-gateway:/tmp/p.py
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py          # 跑一遍并自动恢复
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py --keep   # 留下 open 告警看页面

两种模式都会删掉自己造的 preview_task / access_log 记录；
不加 --keep 时还会把告警置为已恢复（验证 resolve 链路）。
清理告警表: DELETE FROM alert_log;
"""
import asyncio
import sys
import time

from app import alerts, config, db

TAG = "probe-"


async def seed():
    r = await db.get_redis()
    # 1) 多系统同时限流 + 单系统风暴：直接写 ratelimit 的分钟桶
    k = "kk:rlcnt:" + time.strftime("%Y%m%d%H%M")
    await r.hset(k, mapping={"probe-a": 320, "probe-b": 90, "probe-c": 80})
    await r.expire(k, 900)

    # 2) 排队超时 + 队列积压（3 条超时、25 条排队 → 同时命中两条规则）
    for i in range(25):
        await db.execute(
            "INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url,"
            " file_name, status, created_at, updated_at) VALUES (%s,'probe','PROBE',%s,"
            "'http://probe/x.dwg','probe.dwg','queued', NOW() - INTERVAL 8 MINUTE, NOW())",
            (TAG + "q%d" % i, ("p%d" % i).ljust(64, "0")))

    # 3) 加载超时：e2e=1 且 400 秒
    await db.execute(
        "INSERT INTO access_log (request_id, app_id, emp_no, file_key, file_name,"
        " file_ext, action, cost_ms, e2e, status_code, success, created_at)"
        " VALUES (%s,'probe','PROBE',%s,'probe.pptx','pptx','preview',400000,1,200,1,NOW())",
        (TAG + "slow", "s".ljust(64, "0")))

    # 4) 转换异常：10 条 failed + 2 条 ready
    for i in range(10):
        await db.execute(
            "INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url,"
            " file_name, status, created_at, updated_at) VALUES (%s,'probe','PROBE',%s,"
            "'http://probe/y.pptx','probe.pptx','failed', NOW(), NOW())",
            (TAG + "f%d" % i, ("f%d" % i).ljust(64, "0")))
    for i in range(2):
        await db.execute(
            "INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url,"
            " file_name, status, created_at, updated_at) VALUES (%s,'probe','PROBE',%s,"
            "'http://probe/z.pptx','probe.pptx','ready', NOW(), NOW())",
            (TAG + "r%d" % i, ("r%d" % i).ljust(64, "0")))


async def cleanup():
    await db.execute("DELETE FROM preview_task WHERE task_id LIKE 'probe-%%'")
    await db.execute("DELETE FROM access_log WHERE request_id LIKE 'probe-%%'")
    r = await db.get_redis()
    await r.delete("kk:rlcnt:" + time.strftime("%Y%m%d%H%M"))


def fake_cpu(core):
    """塞两个采样点，让 peak_in() 算出指定核数。"""
    now = time.time()
    alerts._cpu_pts.clear()
    alerts._cpu_pts.append((now - 2, 0))
    alerts._cpu_pts.append((now, int(core * 2 * 1e6)))


def fake_mem(gb):
    now = time.time()
    alerts._mem_pts.clear()
    alerts._mem_pts.append((now - 1, int(gb * 1024 ** 3)))


def relax_thresholds():
    """把阈值调到必然触发，验证规则本身；真实阈值只在生产里生效。"""
    config.ALERT_CPU_CORES = 0.2
    config.ALERT_MEM_GB = 0.1
    config.ALERT_DISK_FREE_GB = 99999
    config.ALERT_CACHE_GB = 0.05


def restore_thresholds():
    config.ALERT_CPU_CORES = 8
    config.ALERT_MEM_GB = 10
    config.ALERT_DISK_FREE_GB = 20
    config.ALERT_CACHE_GB = 160


async def main():
    keep = "--keep" in sys.argv      # 保留 open 状态，用于页面渲染验证
    real = "--real" in sys.argv      # 不伪造 CPU/内存/磁盘阈值（截图用，数据全部合理）
    await db.init_db()
    try:
        await seed()
        if not real:
            fake_cpu(3.0)
            fake_mem(9.4)
            relax_thresholds()

        hits = await alerts.evaluate()
        print("\n== 第一轮：10 条规则应全部命中 ==")
        for h in hits:
            print("  [%-8s] %-14s %s  %s=%s/%s %s"
                  % (h["level"], h["kind"], h["target"], h["kind"],
                     h["value"], h["threshold"], h["unit"]))
        print("  命中 %d 条" % len(hits))

        await alerts.apply(hits)
        await cleanup()              # 造的数据立刻删掉，只留告警本身
        rows = await db.query(
            "SELECT kind, title, target, value, threshold, unit, status FROM alert_log"
            " WHERE first_at > NOW() - INTERVAL 5 MINUTE ORDER BY id")
        print("\n== 落库 ==\n  新增 %d 条 open" % len(rows))

        if keep:
            print("\n已保留 %d 条 open 告警，供页面渲染验证（清理: DELETE FROM alert_log）"
                  % len(hits))
            return

        # 第二轮：复原阈值 + 清采样点 → 应该全部自动恢复
        alerts._cpu_pts.clear()
        alerts._mem_pts.clear()
        restore_thresholds()
        hits2 = await alerts.evaluate()
        await alerts.apply(hits2)
        print("\n== 第二轮（清数据 + 复原阈值）==")
        print("  仍命中 %d 条: %s" % (len(hits2), [h["kind"] for h in hits2]))
        left = await db.query_one(
            "SELECT COUNT(*) c FROM alert_log WHERE status='open'")
        print("  未恢复 %d 条" % int((left or {}).get("c") or 0))

        resolved = await db.query(
            "SELECT kind, title, first_at, resolved_at,"
            " TIMESTAMPDIFF(SECOND, first_at, resolved_at) s FROM alert_log"
            " WHERE first_at > NOW() - INTERVAL 10 MINUTE AND status='resolved'"
            " ORDER BY id")
        print("\n== 生命周期 ==")
        for r in resolved:
            print("  %-14s 持续 %ss  %s → %s" % (r["kind"], r["s"],
                                                r["first_at"], r["resolved_at"]))
    finally:
        if not keep:
            await cleanup()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
