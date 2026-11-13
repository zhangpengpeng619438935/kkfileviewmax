#!/usr/bin/env python3
"""验证缓存键是否跟签名变化: 同一文件用不同查询串连续打开。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/real.docx"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=300).read())


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
for q in ("?exp=1&sig=aaa", "?exp=2&sig=bbb", "?exp=3&sig=ccc"):
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + q, "empNo": "EMP0001"}, token)["data"]
    print("%-22s -> %.2fs | fileKey %s" % (q, time.time() - t0, d["fileKey"][:16]))
