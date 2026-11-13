#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量 /preview/url 的接口响应耗时与拿到预览地址的总耗时"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP_ID = "kk-demo"
SECRET = "your-app-secret"


def call(path, payload=None, token=None, timeout=120):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "X-App-Id": APP_ID}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


token = call("/api/v1/auth/token", {"appId": APP_ID, "appSecret": SECRET})["data"]["accessToken"]
samples = "/opt/kkfileview-service/samples"
targets = []
for n in sorted(os.listdir(samples)):
    if n.startswith("real.") or "X6M" in n:
        targets.append(n)

print("%-28s %-10s %-10s %-8s %s" % ("文件", "接口耗时", "拿到地址", "结果", "备注"))
print("-" * 78)
max_wait = int(sys.argv[1]) if len(sys.argv) > 1 else 60
for name in targets:
    url = "http://127.0.0.1:8899/" + urllib.parse.quote(name)
    t0 = time.time()
    d = call("/api/v1/preview/url",
             {"fileUrl": url, "empNo": "EMP0001", "empName": "耗时测试"}, token)["data"]
    api_cost = time.time() - t0
    pv = d.get("previewUrl")
    note = ""
    if not pv and d.get("taskId"):
        tid = d["taskId"]
        waited = 0
        while waited < max_wait:
            time.sleep(1)
            waited += 1
            st = call("/api/v1/preview/status?taskId=%s" % tid, token=token)["data"]
            if st["status"] == "ready":
                pv = st.get("previewUrl")
                note = "轮询 %ds" % waited
                break
            if st["status"] == "failed":
                note = "失败: " + str(st.get("errMsg"))[:40]
                break
        else:
            note = "等待 %ds 仍未完成" % max_wait
    total = time.time() - t0
    print("%-28s %-10s %-10s %-8s %s" % (
        name[:26], "%.2fs" % api_cost, "%.2fs" % total,
        "有地址" if pv else "无地址", note))
