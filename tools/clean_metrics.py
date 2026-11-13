# -*- coding: utf-8 -*-
"""清理埋点污染：看板自身请求被当成业务调用写进了 access_log。

用法：
    python3 tools/clean_metrics.py          只统计，不改
    python3 tools/clean_metrics.py apply    执行清理 + 重建聚合
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app import collector, db  # noqa: E402

APPLY = "apply" in sys.argv


async def main():
    await db.init_db()

    print("=== 当前 access_log 分布 ===")
    rows = await db.query(
        "SELECT app_id, COUNT(*) c, SUM(emp_no='') emp_empty"
        " FROM access_log GROUP BY app_id ORDER BY c DESC")
    for r in rows:
        print("  app_id=%-10s %6d 条 (空工号 %d)" % (r["app_id"] or "(空)", r["c"], r["emp_empty"] or 0))

    # 污染来源有两类：
    #   1) 看板/管理接口 → app_id 为空或 '_all'
    #   2) /api/v1/preview/status 轮询 → 有 app_id 但没有工号（业务调用 empNo 必填）
    BAD = "(emp_no='' OR app_id='' OR app_id='_all')"
    bad = await db.query_one("SELECT COUNT(*) c FROM access_log WHERE " + BAD)
    print("\n  待清理（空工号 / 空 app_id / _all）: %d 条" % (bad["c"] if bad else 0))

    a = await db.query_one(
        "SELECT COUNT(*) c FROM audit_log WHERE app_id='' OR app_id='_all'")
    print("  audit_log 中的同类记录: %d 条" % (a["c"] if a else 0))

    if not APPLY:
        print("\n（只统计。加 apply 参数执行清理）")
        return

    print("\n=== 执行清理 ===")
    n1 = await db.execute("DELETE FROM access_log WHERE " + BAD)
    n2 = await db.execute("DELETE FROM audit_log WHERE app_id='' OR app_id='_all'")
    print("  删除 access_log %d 条 / audit_log %d 条" % (n1 or 0, n2 or 0))

    print("\n=== 重建聚合表（近 180 天） ===")
    for t in ("stat_daily", "stat_weekly", "stat_monthly", "stat_total",
              "stat_file_daily", "stat_latency_hist"):
        await db.execute("DELETE FROM %s" % t)
        print("  已清空 %s" % t)
    await collector.aggregate_once(days_back=180)
    print("  聚合完成")

    print("\n=== 清理后 ===")
    rows = await db.query(
        "SELECT app_id, COUNT(*) c FROM access_log GROUP BY app_id ORDER BY c DESC")
    for r in rows:
        print("  app_id=%-10s %6d 条" % (r["app_id"] or "(空)", r["c"]))
    tot = await db.query_one(
        "SELECT total_cnt total, success_cnt success, fail_cnt fail"
        " FROM stat_total WHERE app_id='_all'")
    if tot:
        print("\n  累计: 总 %s / 成功 %s / 失败 %s"
              % (tot["total"], tot["success"], tot["fail"]))

    await db.close() if hasattr(db, "close") else None


asyncio.run(main())
