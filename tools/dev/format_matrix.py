#!/usr/bin/env python3
"""格式支持矩阵: 逐个文件走真实预览链路, 记录落点与大小。"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
files = sys.argv[1:] or ["hello.txt", "bom_test.csv", "test.jpg", "test.png", "test.gif",
                         "test.bmp", "test.svg", "real.pdf", "real.docx", "real.pptx",
                         "real.xls", "bom_test.xlsx"]
print("%-22s %8s %6s %s" % ("文件", "接口(s)", "大小", "落点/结果"))
for name in files:
    t0 = time.time()
    try:
        d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    except urllib.error.HTTPError as e:
        print("%-22s %8s %6s HTTP %s %s" % (name, "%.2f" % (time.time() - t0), "-", e.code,
                                            e.read().decode("utf-8", "replace")[:60]))
        continue
    api = time.time() - t0
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                      "Authorization": "Bearer " + token}), timeout=180)
        body = r.read()
        final = r.url
    except urllib.error.HTTPError as e:
        print("%-22s %8.2f %6s HTTP %s %s" % (name, api, "-", e.code,
                                              e.read().decode("utf-8", "replace")[:60]))
        continue
    where = ("表格" if "/sheet/" in final else "快速内容页" if "/prog/" in final
             else "pdf.js" if "pdfjs" in final else "引擎")
    note = ""
    text = body[:4000].decode("utf-8", "replace")
    if where == "引擎":
        m = re.search(r"<title>([^<]*)</title>", text)
        note = m.group(1)[:20] if m else ""
        if "暂不支持" in text or "notSupported" in text:
            note = "不支持"
    print("%-22s %8.2f %5dK %s %s" % (name, api, len(body) // 1024, where, note))
