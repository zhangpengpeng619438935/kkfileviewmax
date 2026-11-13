# -*- coding: utf-8 -*-
"""单文件探测：打印 preview/url 的原始返回（排查状态/落点用）。

用法: python3 tools/dev/probe_url.py <fileUrl> [empNo]
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"


def main():
    url = sys.argv[1]
    emp = sys.argv[2] if len(sys.argv) > 2 else "EMP910"
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    tk = json.loads(urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v1/auth/token",
        json.dumps({"appId": "kk-demo", "appSecret": "your-app-secret"}).encode(),
        h), timeout=20).read())["data"]["accessToken"]
    h["Authorization"] = "Bearer " + tk
    t0 = time.time()
    req = urllib.request.Request(
        BASE + "/api/v1/preview/url",
        json.dumps({"fileUrl": url, "empNo": emp, "waitSeconds": 1}).encode(), h)
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=120).read())
    except urllib.error.HTTPError as e:
        print("HTTP %s %.2fs" % (e.code, time.time() - t0))
        print(e.read().decode("utf-8", "replace")[:300])
        return
    d = r.get("data") or {}
    print("%.2fs status=%s mode=%s" % (time.time() - t0, d.get("status"),
                                       d.get("readyMode") or d.get("mode")))
    print("fileKey:", (d.get("fileKey") or "")[:16])
    print("previewUrl:", (d.get("previewUrl") or "")[:80])
    print("其余字段:", {k: v for k, v in d.items()
                     if k not in ("previewUrl", "fileKey")})


if __name__ == "__main__":
    main()
