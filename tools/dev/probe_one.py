# -*- coding: utf-8 -*-
"""对单个 fileUrl 打 preview/url，打印状态与 detail（排查加密/失败用）。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path,
                                 json.dumps(payload).encode() if payload else None, h)
    try:
        r = urllib.request.urlopen(req, timeout=180)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body[:300]}


def main():
    url = sys.argv[1]
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})[1]["data"]["accessToken"]
    t0 = time.time()
    st, body = call("/api/v1/preview/url",
                    {"fileUrl": url, "empNo": "EMP0001", "empName": "验证"}, tk)
    dt = time.time() - t0
    d = body.get("data") if isinstance(body, dict) else None
    print("HTTP %s  %.2fs" % (st, dt))
    if isinstance(body, dict) and body.get("detail"):
        print("detail:", body["detail"])
    if d:
        print("status:", d.get("status"), "mode:", d.get("readyMode") or d.get("mode"))
        print("previewUrl:", (d.get("previewUrl") or "")[:90])


if __name__ == "__main__":
    main()
