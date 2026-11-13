# -*- coding: utf-8 -*-
"""验证看板：全局时间范围 / 审计去重 / 点击跳转筛选。"""
import json
import urllib.error
import urllib.request

B = "http://127.0.0.1:9012"
H = {"X-Admin-Token": "change-me-admin-token"}


def get(path):
    req = urllib.request.Request(B + path, headers=H)
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:160]


print("=== ① 五档全局范围（summary） ===")
for rng in ("hour", "day", "week", "month", "total"):
    code, d = get("/api/v1/stats/summary?range=" + rng)
    if code == 200:
        x = d["data"]
        print("  %-6s %-12s 总 %-5s 成功 %-5s 失败 %-4s 平均 %-5sms 最大 %-6s 人数 %s"
              % (rng, x.get("rangeLabel", ""), x["total"], x["success"], x["fail"],
                 x["avg_cost"], x["max_cost"], x["uniq_emp"]))
    else:
        print("  %-6s HTTP %s %s" % (rng, code, d))

print()
print("=== ② 趋势粒度跟随范围 ===")
for rng in ("hour", "day", "week", "month", "total"):
    code, d = get("/api/v1/stats/trend?range=" + rng)
    if code == 200:
        rows = d["data"]
        print("  %-6s %d 个点  首=%s 末=%s"
              % (rng, len(rows), rows[0]["label"] if rows else "-",
                 rows[-1]["label"] if rows else "-"))
    else:
        print("  %-6s HTTP %s" % (rng, code))

print()
print("=== ③ 各页面接口在 total 范围下 ===")
for p in ("/api/v1/stats/apps?range=total&top=3",
          "/api/v1/stats/files?range=total&top=3",
          "/api/v1/stats/employees?range=total&top=3",
          "/api/v1/stats/latency?range=total",
          "/api/v1/stats/errors?days=180&top=3"):
    code, d = get(p)
    if code != 200:
        print("  %-52s HTTP %s %s" % (p, code, d))
        continue
    v = d["data"]
    if isinstance(v, list):
        print("  %-52s %d 行" % (p, len(v)))
    else:
        print("  %-52s %s" % (p, list(v.keys())[:4]))

print()
print("=== ④ 审计日志筛选（点图表跳转用） ===")
for q, label in (("", "全部"), ("&result=fail", "仅失败"),
                 ("&result=success", "仅成功"),
                 ("&orderBy=cost_desc", "按耗时倒序"),
                 ("&orderBy=cost_asc", "按耗时正序")):
    code, d = get("/api/v1/audit/logs?size=3" + q)
    if code == 200:
        x = d["data"]
        first = x["list"][0] if x["list"] else {}
        print("  %-12s 总 %-4s 首行: %s %sms %s"
              % (label, x["total"], first.get("emp_no", "-"),
                 first.get("cost_ms", "-"), first.get("result", "")))
    else:
        print("  %-12s HTTP %s %s" % (label, code, d))

print()
print("=== ⑤ 审计日志是否还有重复（同一文件同一秒多条 open） ===")
code, d = get("/api/v1/audit/logs?size=200")
if code == 200:
    from collections import Counter
    c = Counter()
    for r in d["data"]["list"]:
        c[(r["emp_no"], r["file_name"], r["created_at"][:19])] += 1
    dup = [(k, v) for k, v in c.items() if v > 1]
    print("  同一秒同一文件重复组: %d 组" % len(dup))
    for k, v in dup[:3]:
        print("    %s x%d" % (k[1][:28], v))
