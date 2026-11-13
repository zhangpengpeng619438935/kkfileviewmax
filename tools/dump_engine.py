#!/usr/bin/env python3
"""抓取引擎 onlinePreview 原始 HTML, 统计关键取流/等待逻辑。"""
import base64
import re
import sys
import urllib.parse
import urllib.request

ENG = "http://kkfileview:8012"
raw = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899/real.docx"
enc = urllib.parse.quote_plus(base64.b64encode(raw.encode()).decode())
html = urllib.request.urlopen(ENG + "/onlinePreview?url=" + enc, timeout=240).read().decode(
    "utf-8", "replace")
print("页面字节:", len(html))
for kw in ("getCorsFile", "viewer.html", "pdfjs", "转换中", "转码", "refresh", "setTimeout",
           "location.href", "onload", "iframe", ".pdf"):
    hits = [m.start() for m in re.finditer(re.escape(kw), html)]
    print("%-16s %d 次" % (kw, len(hits)))
print()
# 打印 iframe 与跳转相关代码
for kw in ("<iframe", "location.href", "setTimeout", "getCorsFile", "viewer"):
    for m in list(re.finditer(re.escape(kw), html))[:3]:
        s = max(0, m.start() - 90)
        print("[%s] ...%s..." % (kw, html[s:m.start() + 140].replace("\n", " ")))
        print()
