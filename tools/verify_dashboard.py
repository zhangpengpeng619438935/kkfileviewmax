# -*- coding: utf-8 -*-
"""验证看板鉴权与数据范围。"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"
ADMIN = "change-me-admin-token"


def call(path, headers):
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:160]


H_ADMIN = {"X-Admin-Token": ADMIN}
H_NONE = {"X-App-Id": "", "Authorization": "Bearer "}

# 取一个普通系统的 token
req = urllib.request.Request(
    BASE + "/api/v1/auth/token",
    data=json.dumps({"appId": "kk-demo", "appSecret": "your-app-secret"}).encode(),
    headers={"Content-Type": "application/json"})
TOK = json.loads(urllib.request.urlopen(req, timeout=30).read())["data"]["accessToken"]
H_APP = {"X-App-Id": "kk-demo", "Authorization": "Bearer " + TOK}

print("=== ① 未登录 ===")
code, body = call("/api/v1/stats/summary?range=day", H_NONE)
print("  summary HTTP", code, "->", str(body)[:90])

print()
print("=== ② 管理员令牌（应看全站 _all） ===")
for rng in ("day", "total"):
    code, d = call("/api/v1/stats/summary?range=" + rng, H_ADMIN)
    if code == 200:
        x = d["data"]
        print("  range=%-5s HTTP 200 appId=%s total=%s 成功=%s 失败=%s"
              % (rng, x["appId"], x["total"], x["success"], x["fail"]))
    else:
        print("  range=%s HTTP %s %s" % (rng, code, d))

print()
print("=== ③ 接入系统 kk-demo（应只看自己） ===")
for rng in ("day", "total"):
    code, d = call("/api/v1/stats/summary?range=" + rng, H_APP)
    if code == 200:
        x = d["data"]
        print("  range=%-5s HTTP 200 appId=%s total=%s" % (rng, x["appId"], x["total"]))
    else:
        print("  range=%s HTTP %s %s" % (rng, code, d))

print()
print("=== ④ 越权尝试: 普通系统指定 appId=_all ===")
code, d = call("/api/v1/stats/summary?range=day&appId=_all", H_APP)
print("  HTTP", code, "-> appId=", d["data"]["appId"] if code == 200 else d)

print()
print("=== ⑤ 趋势是否还翻倍（day 最后一天应与总览一致） ===")
for h, label in ((H_ADMIN, "管理员"), (H_APP, "kk-demo")):
    code, d = call("/api/v1/stats/trend?granularity=day&days=3", h)
    if code == 200 and d["data"]:
        last = d["data"][-1]
        print("  %-8s 最后一天 %s total=%s" % (label, last["label"], last["total"]))
    else:
        print("  %s HTTP %s" % (label, code))
code, d = call("/api/v1/stats/summary?range=day", H_ADMIN)
print("  对照: 总览今日 total =", d["data"]["total"])

print()
print("=== ⑥ 饼图数据（调用系统分布） ===")
for h, label in ((H_ADMIN, "管理员"), (H_APP, "kk-demo")):
    code, d = call("/api/v1/stats/apps?range=day", h)
    if code == 200:
        print("  %-8s %d 个系统: %s" % (label, len(d["data"]),
              [(r["app_id"], r["total"]) for r in d["data"][:4]]))
    else:
        print("  %s HTTP %s" % (label, code))

print()
print("=== ⑦ 审计日志范围 ===")
for h, label in ((H_ADMIN, "管理员"), (H_APP, "kk-demo")):
    code, d = call("/api/v1/audit/logs?size=3", h)
    if code == 200:
        apps = set()
        for r in d["data"]["list"]:
            apps.add(r["app_id"])
        print("  %-8s 总数=%s 本页涉及系统=%s" % (label, d["data"]["total"], apps))
    else:
        print("  %s HTTP %s" % (label, code))

print()
print("=== ⑧ 员工/错误 隔离 ===")
for h, label in ((H_ADMIN, "管理员"), (H_APP, "kk-demo")):
    c1, d1 = call("/api/v1/stats/employees?range=day&top=5", h)
    c2, d2 = call("/api/v1/stats/errors?range=day&top=3", h)
    print("  %-8s 员工=%s 错误组=%s" % (
        label, len(d1["data"]) if c1 == 200 else c1, len(d2["data"]) if c2 == 200 else c2))
