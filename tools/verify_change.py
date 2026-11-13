#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证"源文件改了，定时预转会不会重转"（在 127.0.0.1 宿主机上跑）。

    python3 /opt/kkfileview-service/tools/verify_change.py

只看跳过判定，不真的转：
  未改动 → already=True（跳过）
  touch 后 → already=False（重转）

选一个已经预转过的样例文件，touch 它的 mtime 来模拟"用户更新了文件"。
"""
import argparse
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preconvert as p  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--touch", action="store_true",
                help="顺带测'改了之后'：touch 源文件 mtime（只改时间戳，不动内容）")
a = ap.parse_args()

# 随便挑一个已预转、且扫描得到的真实业务文件
files = p.scan({"dwg"}, 1)
if not files:
    sys.exit("扫描不到 DWG，检查 ROOT")
full, rel, name = files[0]
biz = "%s/%s?path=%s" % (p.BIZ_BASE, urllib.parse.quote(name),
                         urllib.parse.quote(rel, safe="/"))
fk = p.file_key(biz)

print("样例：%s" % name)
print("指纹：%r" % p.src_fp(full))
print("未改动   -> already=%s（True=定时任务跳过）" % p.already(fk, full))
assert p.already(fk, full) is True, "源没变却要重转，会白干活"
print("PASS：源文件没变时不会重复转")

if a.touch:
    os.utime(full, None)
    after = p.already(fk, full)
    print("touch 后 -> already=%s（False=下次定时任务会重转）" % after)
    assert after is False, "mtime 变了却没识别出来"
    print("PASS：源文件变了会被发现")
    print("注意：这份样例的 mtime 已被改动，下次定时预转会重转一次（约 20 秒）")
