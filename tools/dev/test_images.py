#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实测各图片格式能否预览"""
import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP_ID = "kk-demo"
SECRET = "your-app-secret"
FILES = ["test.png", "test.jpg", "test.jpeg", "test.bmp", "test.gif"]


def call(path, payload=None, token=None, timeout=60):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "X-App-Id": APP_ID}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(url, token, timeout=60):
    req = urllib.request.Request(url, headers={"X-App-Id": APP_ID,
                                               "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")


token = call("/api/v1/auth/token", {"appId": APP_ID, "appSecret": SECRET})["data"]["accessToken"]

print("%-12s %-8s %-10s %-8s %s" % ("文件", "接口状态", "预览页", "图片渲染", "说明"))
print("-" * 68)
for name in FILES:
    url = "http://127.0.0.1:8899/" + urllib.parse.quote(name)
    try:
        d = call("/api/v1/preview/url",
                 {"fileUrl": url, "empNo": "EMP0001", "empName": "图片测试"}, token)["data"]
    except Exception as e:
        print("%-12s 申请失败 %s" % (name, e))
        continue
    pv = d.get("previewUrl")
    if not pv and d.get("taskId"):
        for _ in range(10):
            time.sleep(3)
            st = call("/api/v1/preview/status?taskId=%s" % d["taskId"], token=token)["data"]
            if st["status"] in ("ready", "failed"):
                pv = st.get("previewUrl")
                break
    if not pv:
        print("%-12s %-8s %s" % (name, d.get("status"), d.get("errMsg", "未拿到地址")))
        continue
    code, final, page = get(pv, token)
    is_img = ("<img" in page.lower()) or ("imagepreview" in page.lower().replace(" ", "")) \
        or ("picPreview" in page)
    failed = "暂不支持" in page
    note = "图片预览页" if is_img and not failed else ("引擎提示不支持" if failed else "非图片渲染页")
    print("%-12s %-8s %-10s %-8s %s" % (name, d.get("status"), "HTTP %s" % code,
                                        "是" if is_img and not failed else "否", note))
