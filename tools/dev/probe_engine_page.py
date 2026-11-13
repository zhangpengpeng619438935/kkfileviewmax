#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在网关容器内执行: 直接请求引擎, 看它能否下载目标文件并渲染。
用法: docker exec -i kk-gateway python3 - < tools/probe_engine_page.py
"""
import base64
import re
import urllib.parse
import urllib.request

RAW = ("http://127.0.0.1:8080/api/file/raw/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "?path=1-1%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/01%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/"
       "07%20%E4%BE%9B%E5%BA%94%E5%95%86PPAP%E8%B5%84%E6%96%99/C-HUD-W024A%20%20%E4%B8%9C%E9%A3%8E"
       "%E4%B9%98%E7%94%A8%E8%BD%A6S73A%20PPAP/%E5%BB%B8%E7%94%9F%E5%A1%91%E8%83%B6%20%20"
       "%E8%9C%97%E6%9D%86%203110314000169/11-%E5%88%9D%E5%A7%8B%E8%BF%87%E7%A8%8B%E7%A0%94%E7%A9%B6"
       "%EF%BC%88Cpk%20%26%20Ppk%EF%BC%89/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "&exp=1792489834&sig=ea3104dc50181c4d08554882d8e33123")

import sys
mode = sys.argv[1] if len(sys.argv) > 1 else "single"
# single: & 编码成 %26   double: & 编码成 %2526(抵消引擎的 URL 规范化还原)
target = RAW if mode == "single" else RAW.replace("%26", "%2526")
print("模式:", mode)
enc = urllib.parse.quote_plus(base64.b64encode(target.encode()).decode())
url = "http://kkfileview:8012/onlinePreview?url=" + enc
html = urllib.request.urlopen(url, timeout=180).read().decode("utf-8", "replace")

print("页面字节:", len(html))
print("含『暂不支持』:", "暂不支持" in html)
m = re.search(r"Server returned HTTP response code:\s*(\d+)\s+for URL:\s*(\S+)", html)
print("引擎报错:", m.group(0) if m else "无")
print("PDF 预览特征:", bool(re.search(r"viewer|pdf\.js|\.pdf", html, re.I)))
if "暂不支持" in html:
    txt = re.sub(r"<[^>]+>", " ", html)
    txt = re.sub(r"\s+", " ", txt).strip()
    print("页面文本:", txt[:400])
