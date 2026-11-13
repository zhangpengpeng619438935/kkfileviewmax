#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性订正：把"探针等到上限"的历史样本从加载耗时统计里摘出去。

背景
----
预览页的可见性探针有一个最长等待（MAX，普通页 120 秒 / 3D 页 60 秒）。
到点还没看到内容时它会报 phase=timeout，而旧代码对 timeout 与"真的看到了"
一视同仁：都写进 access_log 并置 e2e=1。于是这批样本的 cost_ms 就等于探针
上限本身（实测 23 条挤在 120000 附近，还有几条更早版本留下的 285s/555s）。

后果：看板「最长加载」显示 555930ms（9 分 16 秒），「平均加载耗时」被抬高，
p99 从真实的 60 秒被顶到 130 秒。那不是用户等了这么久，那是探针参数。

新代码已经改掉（见 loadmetric._write_back_timeout：timeout 只写耗时、
不置 e2e，并单独计数）。这个脚本只处理**已经写坏的历史行**。

判据
----
e2e=1 且 cost_ms >= 115000：
    普通页探针上限 120 秒，能测到"内容可见"的样本不可能超过它（超过就说明
    报的是 timeout）。115 秒留了 5 秒余量，覆盖请求发出到计时起点的偏差。
    3D 页上限只有 60 秒，超过 115 秒的 3D 样本同样只可能是 timeout。

用法
----
    python3 tools/fix_timeout_samples.py --dry-run    # 只看会改多少条
    python3 tools/fix_timeout_samples.py              # 真改
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pymysql  # noqa: E402

THRESHOLD_MS = 115000


def conn():
    from app import config
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=config.MYSQL_DB)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    c = conn()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*), MIN(cost_ms), MAX(cost_ms) FROM access_log"
                " WHERE e2e=1 AND cost_ms >= %s", (THRESHOLD_MS,))
    n, lo, hi = cur.fetchone()
    n = int(n or 0)
    print("待订正 %d 条（cost_ms %s ~ %s）" % (n, lo, hi))
    if not n:
        print("没有需要订正的记录")
        return 0
    cur.execute("SELECT DATE(created_at) d, COUNT(*) c FROM access_log"
                " WHERE e2e=1 AND cost_ms >= %s GROUP BY d ORDER BY d",
                (THRESHOLD_MS,))
    for d, cnt in cur.fetchall():
        print("  %s  %d 条" % (d, cnt))
    if a.dry_run:
        print("dry-run，未做任何修改")
        return 0
    cur.execute("UPDATE access_log SET e2e=0 WHERE e2e=1 AND cost_ms >= %s",
                (THRESHOLD_MS,))
    c.commit()
    print("已订正 %d 条：保留 cost_ms 供排查，但不再计入加载耗时统计" % cur.rowcount)
    cur.execute("SELECT COUNT(*), ROUND(AVG(cost_ms)), MAX(cost_ms) FROM access_log"
                " WHERE e2e=1 AND cost_ms > 0")
    print("订正后 e2e 样本: 共 %d 条，均值 %s ms，最大 %s ms" % cur.fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
