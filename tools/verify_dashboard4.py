# -*- coding: utf-8 -*-
"""验证看板三项修复：范围联动、失败明细对齐、详情弹窗数据。"""
import json
import urllib.parse
import urllib.request

B = "http://127.0.0.1:9012"
H = {"X-Admin-Token": "change-me-admin-token"}


def g(path, **q):
    url = B + path
    if q:
        url += "?" + urllib.parse.urlencode({k: v for k, v in q.items() if v != ""})
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=H), timeout=60).read())["data"]


print("=== 1. 各范围下 总览 vs 审计失败 ===")
print("%-8s %8s %8s %8s %8s" % ("范围", "总访问", "失败", "审计失败", "一致"))
for r in ("hour", "day", "week", "month", "total"):
    s = g("/api/v1/stats/summary", range=r)
    a = g("/api/v1/audit/logs", result="fail", range=r, size=1)
    ok = "OK" if int(s["fail"]) == int(a["total"]) else "★不一致"
    print("%-8s %8d %8d %8d %8s" % (r, s["total"], s["fail"], a["total"], ok))

print("\n=== 2. 访问量页数据随范围变化 ===")
print("%-8s %10s %10s %10s" % ("范围", "分布点数", "分布总量", "失败(饼图)"))
for r in ("hour", "day", "week", "month", "total"):
    gran = {"hour": "hour", "day": "day", "week": "week",
            "month": "month", "total": "month"}[r]
    t = g("/api/v1/stats/trend", range=r, granularity=gran)
    s = g("/api/v1/stats/summary", range=r)
    print("%-8s %10d %10d %10d" % (r, len(t), sum(x["total"] for x in t), s["fail"]))
emp = g("/api/v1/stats/employees", range="hour", top=3)
emp2 = g("/api/v1/stats/employees", range="total", top=3)
print("工号 TOP1：近24小时 %s/%s 次 · 近10年 %s/%s 次"
      % (emp[0]["emp_no"] if emp else "-", emp[0]["cnt"] if emp else 0,
         emp2[0]["emp_no"] if emp2 else "-", emp2[0]["cnt"] if emp2 else 0))

print("\n=== 3. 近24小时失败明细（应 7 条） ===")
a = g("/api/v1/audit/logs", result="fail", range="hour", size=50)
print("总数 %d，来源 %s" % (a["total"], a["source"]))
for r in a["list"]:
    print("  %s | %s | %s | %s | %sms | id=%s" % (
        r["created_at"], r["status_code"], r["emp_no"],
        (r["file_name"] or "")[:26], r["cost_ms"], r["id"]))

print("\n=== 4. 失败详情弹窗数据 ===")
if a["list"]:
    d = g("/api/v1/audit/detail", id=a["list"][0]["id"], source=a["source"])
    for k in ("created_at", "emp_no", "emp_name", "dept", "app_id", "file_name",
              "statusCode", "errorMsg", "cost_ms", "upstreamMs", "cacheHit",
              "client_ip", "userAgent", "requestId", "action"):
        print("  %-12s %s" % (k, str(d.get(k))[:80]))

print("\n=== 5. 最慢/最快（点卡片） ===")
slow = g("/api/v1/audit/logs", orderBy="cost_desc", range="hour", size=1)
fast = g("/api/v1/audit/logs", orderBy="cost_asc", range="hour", size=1)
s = g("/api/v1/stats/summary", range="hour")
print("最大延迟卡片 %sms / 日志首行 %sms" % (s["max_cost"], slow["list"][0]["cost_ms"]))
print("最快响应卡片 %sms / 日志首行 %sms" % (s["min_cost"], fast["list"][0]["cost_ms"]))

print("\n=== 6. 审计表详情（source=audit，按 request_id 补错误信息） ===")
au = g("/api/v1/audit/logs", size=200)
errs = [r for r in au["list"] if r["result"] != "success"]
print("审计全量 %d 条，其中失败 %d 条" % (au["total"], len(errs)))
if errs:
    d = g("/api/v1/audit/detail", id=errs[0]["id"], source="audit")
    print("  id=%s statusCode=%s errorMsg=%s" % (
        errs[0]["id"], d.get("statusCode"), str(d.get("errorMsg"))[:60]))
