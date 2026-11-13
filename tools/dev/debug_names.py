#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试：为什么零件名取不到。"""
import sys

sys.path.insert(0, "/opt/kkfileview-service/tools")
import step_split as sp  # noqa: E402
from step_assemble import Assembly  # noqa: E402

src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/x6m.step"
header, ents = sp.load(src)
asm = Assembly(header, ents)
occ = asm.occurrences()
pds = []
for geo, m, name in occ:
    pass
print("实例数", len(occ))

# 找几个 PRODUCT_DEFINITION
form = [(i, b) for i, b in ents.items()
        if b.split("(", 1)[0].strip().startswith("PRODUCT_DEFINITION_FORMATION")]
print("FORMATION 实体", len(form))
i, b = form[0]
print("样例 FORMATION #%d: %s" % (i, b[:150]))
rr = sp.refs(b)
print("  引用:", rr)
for r in rr:
    print("   #%d -> %s" % (r, ents.get(r, "")[:110]))

# 反查：这个 pd 有没有走到 _name_of
pd = None
for r in rr:
    if ents.get(r, "").split("(", 1)[0].strip().startswith("PRODUCT_DEFINITION"):
        pd = r
        break
print("推断 pd =", pd)
if pd:
    print("ents[pd] =", ents.get(pd, "")[:120])
    hit = [x for x, bb in ents.items()
           if bb.split("(", 1)[0].strip().startswith("PRODUCT_DEFINITION_FORMATION")
           and pd in sp.refs(bb)]
    print("引用该 pd 的 FORMATION:", hit)
    print("_name_of(%d) = %r" % (pd, asm._name_of(pd)))
