#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时任务包装器：跑一条命令，失败就把这次失败记进「服务告警」。

**为什么需要它**：`/stats/alerts` 的 11 条规则全是 gateway 进程内的采样
（CPU/内存/磁盘/队列/限流），cron 跑在宿主机、不在那个进程里，所以
备份失败、分区滚动失败、缓存回收失败**一条都不会出现在服务告警里** ——
以前只能靠推送，而现在不用机器人了，等于完全不可见。

用法（cron 里）：

    20 3 * * * cd /opt/kkfileview-service && flock -n /tmp/kk_backup.lock \\
      /usr/bin/python3 tools/cron_run.py backup "数据库备份" -- \\
      /usr/bin/python3 tools/backup_db.py >> /tmp/kk_backup.log 2>&1

行为：
  - 命令退出码 0 → 把该 job 之前的 open 告警置为已恢复
  - 非 0 → 记一条告警（已有 open 的就 hits+1、更新时间，不重复新增）
  - 子进程的 stdout/stderr 原样透传，日志文件照旧
  - 配了 ALERT_WEBHOOK 就顺手推一条（没有也不影响）

**为什么不直接写 MySQL 客户端**：宿主机没有 pymysql。这里走
`docker exec -i kk-mysql` + base64 把 SQL 送进 stdin —— 中文、引号、反斜杠
全都不用转义，也不会在 `ps` 里暴露整条 SQL。密码走 MYSQL_PWD 环境变量。
"""

import base64
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTAINER = "kk-mysql"

# 这些 job 的告警统一用 kind='cron'，target 就是 job key，方便在页面按对象筛
KIND = "cron"
LEVEL = "warn"


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return default


def _sql(text):
    """跑一条 SQL。走 stdin+base64，绕开 shell 与 mysql 的两层转义。"""
    pwd = env("MYSQL_ROOT_PASSWORD")
    db = env("MYSQL_DB", "kkfileview")
    if not pwd:
        raise RuntimeError("读不到 MYSQL_ROOT_PASSWORD")
    os.environ["MYSQL_PWD"] = pwd        # 不出现在命令行/ps 里
    # **必须显式指定字符集**：容器里的 mysql 客户端默认不是 utf8mb4，会把我们发过去
    # 的 UTF-8 字节当 latin1 再转一次存进去（"测试任务" 变成 C3A6C2B5E280B9...）。
    # 恶心的是 CLI 显示时又反向转一次，看着完全正常 —— 只有看板用 utf8 读才暴露。
    cmd = ["docker", "exec", "-i", "-e", "MYSQL_PWD", CONTAINER,
           "sh", "-c",
           "base64 -d | mysql -uroot --batch --skip-column-names"
           " --default-character-set=utf8mb4 %s" % db]
    p = subprocess.run(cmd, input=base64.b64encode(text.encode("utf-8")),
                       capture_output=True, timeout=60)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError([x for x in err.splitlines() if "Warning" not in x][:2])
    return (p.stdout or b"").decode("utf-8", "replace").strip()


def _q(s, limit=500):
    """SQL 字符串字面量：单引号翻倍。反斜杠在 ASCII 模式会转义，这里先统一处理。"""
    s = (s or "")[:limit].replace("\\", "\\\\").replace("'", "''")
    return "'" + s + "'"


def resolve(job):
    """任务成功了：把之前 open 的告警标为已恢复。返回是否真的有告警被恢复。"""
    rows = _sql(
        "SELECT id FROM alert_log WHERE kind=%s AND target=%s AND status='open'"
        % (_q(KIND), _q(job)))
    if not rows:
        return False
    _sql("UPDATE alert_log SET status='resolved', resolved_at=NOW(), last_at=NOW()"
         " WHERE kind=%s AND target=%s AND status='open'" % (_q(KIND), _q(job)))
    return True


def record(job, title, detail):
    """任务失败了：有 open 的就累加，没有就新开一条。"""
    rows = _sql("SELECT id FROM alert_log WHERE kind=%s AND target=%s"
                " AND status='open' ORDER BY id DESC LIMIT 1" % (_q(KIND), _q(job)))
    if rows:
        _sql("UPDATE alert_log SET last_at=NOW(), hits=hits+1, detail=%s WHERE id=%s"
             % (_q(detail), rows.splitlines()[0]))
        return False                     # False = 不是新告警
    _sql("INSERT INTO alert_log (kind, level, title, target, value, peak, threshold,"
         " unit, detail, hits, status, first_at, last_at) VALUES"
         " (%s,%s,%s,%s,1,1,0,'',%s,1,'open',NOW(),NOW())"
         % (_q(KIND), _q(LEVEL), _q(title, 128), _q(job, 128), _q(detail)))
    return True                          # True = 新开的告警


def push(title, detail):
    """可选推送。没有配置通道时静默返回。"""
    try:
        sys.path.insert(0, HERE)
        import notify
        notify.send("⚠️ **%s**\n\n> %s" % (title, detail.replace("\n", "\n> ")))
    except Exception:
        pass                             # 推送是尽力而为，绝不影响记录


def tail_of(path, n=6):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    # **手动按 `--` 切分，不用 argparse.REMAINDER**：REMAINDER 在位置参数之后会
    # 把 `--log` 也一起吃掉，子命令就成了 `['--log', '/tmp/x.log', ...]`，
    # 报 "No such file or directory: '--log'"。
    if "--" not in argv:
        print("cron_run: 缺少 -- 分隔符（用法见文件头）", file=sys.stderr)
        return 2
    cut = argv.index("--")
    head, cmd = argv[:cut], argv[cut + 1:]
    if len(head) < 2:
        print("cron_run: 需要两个位置参数：job、title", file=sys.stderr)
        return 2
    job, title = head[0], head[1]
    log = ""
    if "--log" in head:
        k = head.index("--log")
        if k + 1 < len(head):
            log = head[k + 1]
    if not cmd:
        print("cron_run: 缺少要执行的命令", file=sys.stderr)
        return 2

    t0 = time.time()
    rc = subprocess.call(cmd)            # stdout/stderr 直接透传，日志照旧
    cost = time.time() - t0

    if rc == 0:
        try:
            if resolve(job):
                print("cron_run: %s 本次成功，已恢复之前的告警" % job)
                push("%s 已恢复" % title, "本次执行成功（耗时 %.0fs）" % cost)
        except Exception as exc:
            print("cron_run: 记录恢复状态失败（不影响任务结果）: %s" % exc,
                  file=sys.stderr)
        return 0

    detail = "退出码 %d，耗时 %.0fs" % (rc, cost)
    if log:
        tail = tail_of(log)
        if tail:
            detail += "；日志尾部：" + tail
    detail = detail[:500]                # 表里是 VARCHAR(512)
    try:
        is_new = record(job, title, detail)
        print("cron_run: %s 失败（退出码 %d），已%s服务告警" %
              (job, rc, "新增" if is_new else "累加到现有"))
        if is_new:
            push("%s 失败" % title, detail)
    except Exception as exc:
        # 连告警都记不进去时，至少让 cron 日志里有一句显眼的
        print("!!! cron_run: 写入服务告警失败: %s" % exc, file=sys.stderr)
        push("%s 失败（且告警入库失败）" % title, detail)
    return rc


if __name__ == "__main__":
    sys.exit(main())
