#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证预转结果：逐个用**业务 URL**申请预览，看是否 ready、耗时多少。

这是在模拟用户点击（业务链接带签名，签名被 file_key 剔除，命中同一个缓存）。
不带签名也能用：ready 的文件在 preview/url 里不会去探源站，直接返回预览地址。
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preconvert import BIZ_BASE, ROOT, file_key, scan  # noqa: E402

API = "http://127.0.0.1:9012"


def post(path, body, token=None):
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-App-Id": "kk-demo",
                 **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    exts = {"dwg", "dxf"} if len(sys.argv) < 2 else {sys.argv[1]}
    tk = post("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    files = scan(exts, 0)
    print("共 %d 个" % len(files))
    ready = slow = 0
    worst = 0
    for full, rel, name in files:
        url = "%s/%s?path=%s" % (BIZ_BASE, urllib.parse.quote(name),
                                 urllib.parse.quote(rel, safe="/"))
        _fk = file_key(url)
        t0 = time.time()
        try:
            d = post("/api/v1/preview/url", {"fileUrl": url, "empNo": "VERIFY",
                                             "empName": "验证", "fileName": name}, tk)
            ms = int((time.time() - t0) * 1000)
            st = (d.get("data") or {}).get("status") or d.get("msg")
        except Exception as exc:
            st, ms = "ERR %s" % str(exc)[:40], int((time.time() - t0) * 1000)
        worst = max(worst, ms)
        time.sleep(0.35)          # 避开 kk-demo 的 QPS 限流（否则后一半全是 429）
        if st == "ready":
            ready += 1
        else:
            print("  未就绪 %-46s %-12s %5dms  fk=%s" % (name[:44], st, ms, _fk[:12]))
        if ms > 300:
            slow += 1
    print("\nready %d / %d   >300ms %d   最慢 %dms" % (ready, len(files), slow, worst))


if __name__ == "__main__":
    main()
