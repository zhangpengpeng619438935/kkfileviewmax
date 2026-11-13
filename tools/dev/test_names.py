#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证拆件后能否拿到真实零件名（PRODUCT 名，含中文解码）。"""
import sys

sys.path.insert(0, "/opt/kkfileview-service/tools")
import step_split as sp  # noqa: E402
from step_assemble import Assembly  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/x6m.step"
header, ents = sp.load(src)
asm = Assembly(header, ents)
occ = asm.occurrences()
names = [n for _, _, n in occ if n]
print("实例 %d 个，有名字的 %d 个" % (len(occ), len(names)))
seen = []
for n in names:
    if n not in seen:
        seen.append(n)
    if len(seen) >= 12:
        break
print("去重后的名字样例：")
for n in seen:
    print("  ", n)
