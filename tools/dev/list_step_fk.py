#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出扫描到的 STEP 类文件及其 fileKey（排查空模型用）。

输出制表符分隔：fk \t 大小MB \t 相对路径
"""
import os
import sys
import urllib.parse

sys.path.insert(0, "/opt/kkfileview-service/tools")
import preconvert_step as P  # noqa: E402
from preconvert import BIZ_BASE, file_key  # noqa: E402

files = P.scan(0, 0, 0, "")
print("TOTAL\t%d" % len(files), flush=True)
for full, rel, fn, mb in files:
    biz_url = "%s/%s?path=%s" % (BIZ_BASE, urllib.parse.quote(fn),
                                 urllib.parse.quote(rel, safe="/"))
    print("%s\t%.2f\t%s" % (file_key(biz_url), mb, rel), flush=True)
