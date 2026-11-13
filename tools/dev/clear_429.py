#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉"429 限流"造成的失败冷却（这类是临时的，不该冻结 1~30 天）。

用法（宿主机）: python3 /tmp/clear_429.py [--dry]
"""
import sys

sys.path.insert(0, "/opt/kkfileview-service/tools")
import prestate  # noqa: E402

dry = "--dry" in sys.argv
out = prestate._rcli("--scan", "--pattern", "'kk:pre:fail:*'").stdout.split()
hit = 0
for k in out:
    fk = k.rsplit(":", 1)[-1]
    st = prestate._load(fk)
    if not st:
        continue
    err = str(st.get("err", ""))
    if "429" in err or "超出配额" in err:
        hit += 1
        if not dry:
            prestate._drop(fk)
print("命中 429 冷却 %d 个，%s" % (hit, "只统计（dry）" if dry else "已清除"))
