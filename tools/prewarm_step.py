#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STEP 热点文件预转换：把"经常被打开但还没转过实体"的装配体提前排进队列。

为什么单独做：kk 自带的那套预热（prewarm_hot.py）只暖引擎缓存（PDF/图片），
不碰我们自己的两步管线（wire → mesh）。而大装配体首次转换要 1~2 分钟，
用户点开就得干等。夜里转好，白天点开就是秒开。

用法：
    python3 tools/prewarm_step.py --dry                 # 只看会排哪些
    python3 tools/prewarm_step.py --top 40 --max-mb 120 # 真排（默认 30 天热点）
    python3 tools/prewarm_step.py --days 60 --top 80
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import prewarm_hot as ph          # 复用热点统计（access_log + 源地址兜底）

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
STEP_EXTS = ("step", "stp", "p21")


def redis(*args):
    r = subprocess.run(["docker", "exec", "kk-redis", "redis-cli", "-n", "3"] + list(args),
                       capture_output=True, text=True, timeout=20)
    return (r.stdout or "").strip()


def have_product(fk):
    """已有网格或线框就不用再排；只有线框也排（还缺实体）。"""
    return os.path.exists(os.path.join(SHEETS, fk + ".stepmesh.gz"))


def remote_size(url):
    """HEAD 拿 Content-Length；拿不到就当 0（不拦）。"""
    try:
        r = subprocess.run(["curl", "-sSI", "--max-time", "8", url],
                           capture_output=True, text=True, timeout=12)
        for line in (r.stdout or "").splitlines():
            if line.lower().startswith("content-length:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="统计多少天内的打开次数")
    ap.add_argument("--top", type=int, default=40, help="最多排多少个文件")
    ap.add_argument("--max-mb", type=int, default=150, help="超过这个体积不排（夜里也吃不消）")
    ap.add_argument("--max-total-mb", type=int, default=600,
                    help="这一轮最多排多少 MB（夜里也吃不消就靠这个兜住）")
    ap.add_argument("--dry", action="store_true", help="只打印，不入队")
    a = ap.parse_args()

    # 先取一大把热点（各种类型混在一起），再本地筛 STEP：直接按扩展名过滤
    # 会把 Top 挤空（STEP 通常不是打开次数最多的类型）
    files, no_url = ph.hot_files(a.days, 800)
    steps = [f for f in files if (f["ext"] or "").lower() in STEP_EXTS]
    print("热点文件 %d 个，其中 STEP 类 %d 个（源地址缺失 %d 个已跳过）"
          % (len(files), len(steps), no_url))

    todo, skip_done, skip_big = [], 0, 0
    for f in steps:
        if len(todo) >= a.top:
            break
        if have_product(f["fk"]):
            skip_done += 1
            continue
        mb = remote_size(f["url"]) / 1048576.0
        if a.max_mb and mb > a.max_mb:
            skip_big += 1
            print("  跳过（%.0fMB > %dMB）  %s" % (mb, a.max_mb, (f["name"] or "")[:50]))
            continue
        todo.append((f, mb))

    # 未知体积（HEAD 被拒）按 60MB 估，且排在小文件之后：
    # 万一真是个几百 MB 的巨物，也不会卡在队首把后面的全堵住
    todo.sort(key=lambda x: (x[1] if x[1] > 0 else 60.0))

    budget, picked = 0.0, []
    for f, mb in todo:
        cost = mb if mb > 0 else 60.0
        if budget + cost > a.max_total_mb:
            print("  超出本轮预算 %dMB，暂不排  %s" % (a.max_total_mb, (f["name"] or "")[:48]))
            continue
        budget += cost
        picked.append((f, mb))
    print("已转好 %d 个，超限 %d 个，待排 %d 个（合计约 %.0f MB）"
          % (skip_done, skip_big, len(picked), budget))
    for f, mb in picked:
        print("  %4d 次  %6.1fMB  %s" % (f["opens"], mb, (f["name"] or f["url"])[:56]))

    if a.dry:
        return 0

    queued = 0
    for f, _mb in picked:
        # 和网关的 _step_enqueue 用同一个去重键，避免重复排
        if not redis("set", "kk:step:q:" + f["fk"], "1", "NX", "EX", "7200"):
            print("  已在队列  %s" % (f["name"] or "")[:50])
            continue
        redis("lpush", "kk:step:need", "%s|%s" % (f["fk"], f["url"]))
        queued += 1
        print("  已入队  %s" % (f["name"] or "")[:56])
    print("共入队 %d 个（worker 会按队列顺序逐个转换）" % queued)
    return 0


if __name__ == "__main__":
    sys.exit(main())
