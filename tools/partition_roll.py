#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""access_log / audit_log 按天分区滚动 —— 补未来，也补历史缺口。

**为什么重写**：原 partition_roll.sh 用 `MAX(PARTITION_DESCRIPTION)` 判断"覆盖到哪天"，
只要最后一天够远就认为健康。实测 2026-11-12 的状态是"已覆盖到 11-19"，
但中间缺 10-27、11-13、11-15、11-17 四天 —— 缺的那天会被**前一个分区连带装下**
（p20261028 里同时有 10-27 的 426 行和 10-28 的 539 行）。后果：

  - 按天 DROP PARTITION 会连带删掉相邻一天的数据；
  - 分区裁剪对这那天的查询失效（要扫整个两天分区）。

缺口一旦产生就永远补不上，因为脚本只会"从最大上界往后加"。

现在改成"逐日核对"：目标区间内哪天没有独立分区，就把装它的那个分区拆开
（REORGANIZE 会自动把对应日期的行搬进新分区）。幂等，可以反复跑。

用法：
    python3 tools/partition_roll.py            # 真做
    python3 tools/partition_roll.py --dry-run  # 只打印会做什么
"""
import argparse
import datetime
import os
import subprocess
import sys

TABLES = ("access_log", "audit_log")
AHEAD_DAYS = 7
MYSQL_CONTAINER = "kk-mysql"


def _env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    envf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(envf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return default


DB = _env("MYSQL_DB", "kkfileview")
PWD = _env("MYSQL_ROOT_PASSWORD")


def mysql(sql, db=True, check=True):
    cmd = ["docker", "exec", MYSQL_CONTAINER, "mysql", "-uroot", "-p" + PWD, "-N", "-B"]
    if db:
        cmd.append(DB)
    cmd += ["-e", sql]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if check and p.returncode != 0:
        msg = (p.stderr or "").strip().splitlines()
        msg = [m for m in msg if "Warning" not in m]
        raise RuntimeError(msg[0] if msg else "mysql 失败")
    return (p.stdout or "").strip()


def day_num(day: str) -> int:
    """TO_DAYS 的等价物（与 MySQL 的 TO_DAYS 同源，用 SQL 校准一次）。"""
    return int(mysql("SELECT TO_DAYS('%s')" % day))


def describe(table):
    """[(分区名, 上界日期 or None)]，按分区顺序。None = pmax。"""
    out = mysql("SELECT IFNULL(PARTITION_NAME,''), IFNULL(PARTITION_DESCRIPTION,'') "
                "FROM information_schema.PARTITIONS WHERE TABLE_SCHEMA='%s' "
                "AND TABLE_NAME='%s' AND PARTITION_NAME IS NOT NULL "
                "ORDER BY PARTITION_ORDINAL_POSITION" % (DB, table))
    parts = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, desc = (line.split("\t") + [""])[:2]
        if name == "pmax" or desc.upper() == "MAXVALUE":
            parts.append((name, None))
            continue
        # 描述值是 TO_DAYS 数，换算回日期
        d = mysql("SELECT FROM_DAYS(%s)" % desc)
        parts.append((name, d))
    return parts


def split_partition(table, target, target_desc, day, dry):
    """把 target 拆出 day 这一天。target_desc 为 None 表示 pmax。"""
    part = "p" + day.replace("-", "")
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    if target_desc is None:
        sql = ("ALTER TABLE %s REORGANIZE PARTITION %s INTO ("
               "PARTITION %s VALUES LESS THAN (TO_DAYS('%s')),"
               "PARTITION %s VALUES LESS THAN MAXVALUE)" % (table, target, part, nxt, target))
    else:
        td = mysql("SELECT TO_DAYS('%s')" % target_desc)
        sql = ("ALTER TABLE %s REORGANIZE PARTITION %s INTO ("
               "PARTITION %s VALUES LESS THAN (TO_DAYS('%s')),"
               "PARTITION %s VALUES LESS THAN (%s))" % (table, target, part, nxt, target, td))
    if dry:
        print("    [dry] %s 拆 %s → +%s" % (table, target, part))
        return True
    mysql(sql)
    return True


def fix_table(table, today, dry):
    parts = describe(table)
    daily = [(n, d) for n, d in parts if d]
    if not daily:
        print("  %s 没有按天分区，跳过" % table)
        return 0
    first_day = min(d for _, d in daily)
    # 目标区间：最早那天的**前一天**（= p_start 上界）到 today+AHEAD
    start = datetime.date.fromisoformat(first_day)
    end = today + datetime.timedelta(days=AHEAD_DAYS)
    missing = []
    d = start
    while d <= end:
        name = "p" + d.strftime("%Y%m%d")
        if name not in {n for n, _ in daily}:
            missing.append(d.isoformat())
        d += datetime.timedelta(days=1)
    if not missing:
        print("  %s 分区连续，覆盖到 %s" % (table, max(x for _, x in daily)))
        return 0
    print("  %s 缺 %d 天: %s" % (table, len(missing), ", ".join(missing)))
    added = 0
    for day in missing:
        parts = describe(table)
        want = int(mysql("SELECT TO_DAYS('%s')" % day))
        target = None
        for n, desc in parts:
            if desc is None:
                target = (n, None)
                break
            if int(mysql("SELECT TO_DAYS('%s')" % desc)) > want:
                target = (n, desc)
                break
        if not target:
            print("    ! %s 找不到容纳 %s 的分区" % (table, day))
            continue
        if target[1] == day:
            # 该分区上界正好是 day，说明它装的是 day-1 —— 顺序错乱，不动
            print("    ! %s 的分区边界与 %s 冲突，人工检查" % (target[0], day))
            continue
        if split_partition(table, target[0], target[1], day, dry):
            added += 1
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = datetime.date.today()
    print("%s 分区滚动（目标覆盖到 %s）"
          % ("[dry-run]" if args.dry_run else "[run]", today + datetime.timedelta(days=AHEAD_DAYS)))
    total = 0
    for t in TABLES:
        try:
            total += fix_table(t, today, args.dry_run)
        except Exception as exc:
            print("  %s 处理失败: %s" % (t, exc))
            return 1
    print("完成，共处理 %d 个分区" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
