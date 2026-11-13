# -*- coding: utf-8 -*-
"""检查看板总览数据为什么看不到。"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"


def get(path, token=None, app="kk-demo"):
    h = {}
    if token:
        h["X-App-Id"] = app
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


print("=== ① 未登录取总览 ===")
code, body = get("/api/v1/stats/summary?range=day")
print("HTTP", code, "->", body[:200])

print()
print("=== ② 登录后取总览 ===")
req = urllib.request.Request(
    BASE + "/api/v1/auth/token",
    data=json.dumps({"appId": "kk-demo", "appSecret": "your-app-secret"}).encode(),
    headers={"Content-Type": "application/json"})
tok = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["accessToken"]
for rng in ("day", "week", "month", "total"):
    code, body = get("/api/v1/stats/summary?range=" + rng, tok)
    print("range=%-6s HTTP %s -> %s" % (rng, code, body[:180]))

print()
print("=== ③ 其它总览接口 ===")
for p in ("/api/v1/stats/trend?granularity=day",
          "/api/v1/stats/apps?range=day",
          "/api/v1/stats/files?range=day",
          "/api/v1/stats/latency?range=day",
          "/api/v1/stats/errors?range=day",
          "/api/v1/health/detail"):
    code, body = get(p, tok)
    print("%-38s HTTP %s -> %s" % (p, code, body[:120].replace("\n", " ")))

print()
print("=== ④ 不做登录/直接看统计库 ===")
