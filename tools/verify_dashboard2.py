# -*- coding: utf-8 -*-
"""验证看板新增的系统名称 / 失败原因 / 系统下钻。"""
import json
import urllib.error
import urllib.request

B = "http://127.0.0.1:9012"
ADMIN = {"X-Admin-Token": "change-me-admin-token"}


def get(path, headers):
    req = urllib.request.Request(B + path, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]


def post(path, payload):
    req = urllib.request.Request(
        B + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


tok = post("/api/v1/auth/token",
           {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
APP = {"X-App-Id": "kk-demo", "Authorization": "Bearer " + tok}

for name, h in (("管理员", ADMIN), ("kk-demo", APP)):
    print("=" * 56)
    print(name)

    code, d = get("/api/v1/stats/employees?range=day&top=3", h)
    if code == 200:
        print("  工号列表: 字段", sorted(d["data"][0].keys()) if d["data"] else "(空)")
        for r in d["data"][:3]:
            print("    %s / %s / %s / 系统=%s(%s) / %s 次"
                  % (r["emp_no"], r["emp_name"], r["dept"],
                     r["app_name"], r["app_id"], r["cnt"]))
    else:
        print("  工号列表 HTTP", code, d)

    code, d = get("/api/v1/stats/files?range=day&top=3", h)
    if code == 200:
        print("  文件列表: 字段", sorted(d["data"][0].keys()) if d["data"] else "(空)")
        for r in d["data"][:3]:
            print("    %s | 系统=%s | %s 次"
                  % ((r["file_name"] or "")[:24], r["apps"], r["view_cnt"]))
    else:
        print("  文件列表 HTTP", code, d)

    code, d = get("/api/v1/stats/errors?days=180&top=3", h)
    if code == 200:
        print("  失败原因(180天): %d 类" % len(d["data"]))
        for r in d["data"][:3]:
            print("    %s | %s | %s 次 | 系统=%s | 影响 %s 人"
                  % (r["status_code"], (r["error_msg"] or "(无)")[:34],
                     r["cnt"], r["apps"], r["uniq_emp"]))
    else:
        print("  失败原因 HTTP", code, d)

    code, d = get("/api/v1/stats/apps?range=day&top=5", h)
    if code == 200:
        print("  系统分布: %s" % [(r["app_id"], r["total"]) for r in d["data"]])
    else:
        print("  系统分布 HTTP", code, d)
    print()

# 系统下钻：按 appId 查审计日志
print("=" * 56)
print("系统下钻（审计日志按 appId 过滤）")
for aid in ("kk-demo", "kb"):
    code, d = get("/api/v1/audit/logs?appId=%s&size=2" % aid, ADMIN)
    if code == 200:
        apps = sorted({r["app_id"] for r in d["data"]["list"]})
        print("  appId=%-8s 总 %-5s 条, 本页系统=%s" % (aid, d["data"]["total"], apps))
    else:
        print("  appId=%s HTTP %s %s" % (aid, code, d))
