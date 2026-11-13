#!/usr/bin/env python3
"""验收: 4 个文档的打开耗时(冷/热)与落点。"""
import json
import sys
import time
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


def open_one(token, name):
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    api = time.time() - t0
    t1 = time.time()
    r = urllib.request.urlopen(urllib.request.Request(
        d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                  "Authorization": "Bearer " + token}), timeout=300)
    page = r.read()
    view = time.time() - t1
    kind = ("表格" if "/sheet/" in r.url else "快速内容页" if "/prog/" in r.url else "pdf.js" if "pdfjs" in r.url
            else "图片" if "picturesPreview" in r.url else "引擎")
    return api, view, kind, len(page)


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
files = sys.argv[1:] or ["apqp.xlsx", "bom.xlsx", "ehs.pptx", "wire.pdf"]
print("%-12s %9s %9s %9s %9s  %s"
      % ("文件", "冷接口(s)", "冷首屏(s)", "暖接口(s)", "暖首屏(s)", "落点"))
for f in files:
    try:
        c1, v1, k1, s1 = open_one(token, f)
        c2, v2, k2, s2 = open_one(token, f)
        print("%-12s %9.2f %9.2f %9.2f %9.2f  %s (%dKB)"
              % (f, c1, v1, c2, v2, k1, s2 // 1024))
    except Exception as exc:
        print("%-12s 失败: %s" % (f, exc))
    sys.stdout.flush()
