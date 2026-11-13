# -*- coding: utf-8 -*-
"""测单个文件的转换耗时：申请 → 同步等待 → 就绪。

用法: python tools/measure_convert.py <fileUrl> [waitSec]
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:9012"


def post(path, body, token=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-App-Id": "kk-demo",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


url = sys.argv[1]
wait = int(sys.argv[2]) if len(sys.argv) > 2 else 180
tk = post("/api/v1/auth/token",
          {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
t0 = time.time()
d = post("/api/v1/preview/url", {"fileUrl": url, "empNo": "EMP0001",
                                 "empName": "M", "wait": True, "waitSeconds": wait}, tk)
cost = time.time() - t0
data = d.get("data") or {}
print("code=%s status=%s waited=%s 总 %.2fs" % (
    d.get("code"), data.get("status"), data.get("waitedSeconds"), cost))
if d.get("code"):
    print("msg:", d.get("msg"))
print("fileKey:", data.get("fileKey"), "partial:", data.get("partial"))
