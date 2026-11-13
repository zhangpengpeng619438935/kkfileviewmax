#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量各格式的"首次响应"耗时，拆解到每一跳。

用法: python measure_first_response.py <文件名> [<文件名> ...]
输出: preview/url 耗时、跳转链、首屏页面是否立即可见、内容就绪总耗时。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def post(path, payload, token=None, timeout=120):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(), h)
    t0 = time.time()
    body = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return body, time.time() - t0


def get(url, follow=False, timeout=120):
    op = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(NoRedirect)
    t0 = time.time()
    try:
        r = op.open(url, timeout=timeout)
        return r.status, dict(r.headers), r.read(), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(), time.time() - t0


tk = post("/api/v1/auth/token",
          {"appId": "kk-demo", "appSecret": "your-app-secret"})[0]["data"]["accessToken"]

print("%-22s %-10s %-9s %-9s %-9s %s" %
      ("文件", "status", "url响应", "首屏页", "内容就绪", "落点"))
print("-" * 96)
for name in (sys.argv[1:] or ["test_archive.zip"]):
    url = SRC + urllib.parse.quote(name, safe="")
    try:
        d, t_api = post("/api/v1/preview/url",
                        {"fileUrl": url, "empNo": "EMP0001", "empName": "测速"}, tk)
    except urllib.error.HTTPError as e:
        print("%-22s %-10s %s" % (name[:20], "ERR", e.read()[:80]))
        continue
    data = d["data"]
    st, hdr, body, t_page = get(data["previewUrl"])
    loc = hdr.get("location", "")
    hop = loc.split("?")[0]
    # 首屏页（不管是不是等待页）是否 200 且非空
    if loc:
        st2, _, body2, t_page2 = get(BASE + loc if loc.startswith("/") else loc)
        hop = loc.split("?")[0]
        page_cost = t_page + t_page2
        is_wait = b"\xe6\xad\xa3\xe5\x9c\xa8\xe8\xbd\xac\xe6\x8d\xa2" in body2  # 正在转换
    else:
        page_cost, is_wait = t_page, False

    # 内容就绪总耗时: 轮询 ready
    kkt = loc.split("kkt=")[1] if "kkt=" in loc else ""
    fk = data["fileKey"]
    t_ready = -1
    t_start = time.time()
    for _ in range(120):
        if data.get("status") == "ready" and not data.get("provisional"):
            t_ready = 0
            break
        time.sleep(0.5)
        try:
            r = json.loads(get("%s/api/v1/preview/ready?f=%s&kkt=%s"
                               % (BASE, fk, urllib.parse.quote(kkt, safe="")))[2])
        except Exception:
            continue
        if r.get("ready"):
            t_ready = time.time() - t_start
            break
    print("%-22s %-10s %7.2fs %8.2fs %8s %s%s" %
          (name[:20], data.get("status"), t_api, page_cost,
           ("%.1fs" % t_ready) if t_ready >= 0 else ">60s", hop[:26],
           "  [等待页]" if is_wait else ""))
