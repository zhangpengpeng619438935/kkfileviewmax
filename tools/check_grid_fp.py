#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读体检：现有网格/产物的指纹与源文件是否一致（不做任何转换或删除）。

TTL 调短之后，每份产物到期都会走一次校验。指纹对不上就会被判定"源文件变了"、
删掉产物重转 —— 对 STEP 来说那是 20 秒的重新解析。所以先确认指纹是干净的。

    python3 /opt/kkfileview-service/tools/check_grid_fp.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preconvert as p  # noqa: E402
import preconvert_step as ps  # noqa: E402

bad = []
ok = 0
for full, rel, fn, mb in ps.scan(0, 0, 0, ""):
    fk = ps.file_key("%s/%s?path=%s" % (
        ps.BIZ_BASE, __import__("urllib.parse").parse.quote(fn),
        __import__("urllib.parse").parse.quote(rel, safe="/")))
    gz = os.path.join(ps.SHEETS, fk + ".stepmesh.gz")
    if not (os.path.exists(gz) and os.path.getsize(gz) > 1024):
        continue
    saved = p.redis("get", "kk:srcfp:" + fk)
    cur = p.src_fp(full)
    if saved == cur:
        ok += 1
    else:
        bad.append((fn[:40], saved, cur))

print("网格 %d 个指纹一致" % ok)
print("指纹不一致/缺失 %d 个" % len(bad))
for fn, s, c in bad[:5]:
    print("  %-40s 存=%r 现=%r" % (fn, s, c))
if bad:
    print("\n这些会在到期后被重解析一次（约 20 秒/个）。若只是存量老数据，"
          "跑一次 `preconvert_step.py --force` 重写指纹更省事。")
