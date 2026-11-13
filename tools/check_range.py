#!/usr/bin/env python3
"""检查本地 PDF 端点是否支持 Range(pdf.js 分片加载依赖它)。"""
import json
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=300).read())


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
d = call("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/real.docx", "empNo": "EMP0001"}, token)["data"]
tk = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query)["token"][0]
url = "%s/pdf/%s?kkt=%s" % (BASE, d["fileKey"], urllib.parse.quote(tk))

for rng in (None, "bytes=0-1023", "bytes=1000000-1001999"):
    h = {"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}
    if rng:
        h["Range"] = rng
    r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120)
    body = r.read()
    print("%-24s HTTP %s | %7d 字节 | content-range=%s | accept-ranges=%s | len=%s"
          % (rng or "(无 Range)", r.status, len(body),
             r.headers.get("content-range", "-"), r.headers.get("accept-ranges", "-"),
             r.headers.get("content-length", "-")))
