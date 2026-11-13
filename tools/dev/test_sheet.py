#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实的大 Excel 文件验证表格模式(不转 PDF, 直接渲染行列)"""
import json
import os
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP_ID = "kk-demo"
SECRET = "your-app-secret"
SAMPLES = "/opt/kkfileview-service/samples"


def call(path, payload=None, token=None, timeout=120):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "X-App-Id": APP_ID}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def get(url, token, timeout=120):
    req = urllib.request.Request(url, headers={"X-App-Id": APP_ID,
                                               "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read().decode("utf-8", "replace")


name = [n for n in os.listdir(SAMPLES) if "X6M" in n][0]
url = "http://127.0.0.1:8899/" + urllib.parse.quote(name)
print("文件:", name)
print("编码后 URL 长度:", len(url))

token = call("/api/v1/auth/token", {"appId": APP_ID, "appSecret": SECRET})["data"]["accessToken"]

t0 = time.time()
res = call("/api/v1/preview/url",
           {"fileUrl": url, "empNo": "EMP0001", "empName": "验证", "dept": "示例事业部"}, token)
d = res.get("data", {})
print("首次申请: status=%s urlFixed=%s" % (d.get("status"), d.get("urlFixed")))
if "detail" in res:
    print("错误:", res["detail"][:300])
    raise SystemExit(1)

pv = d.get("previewUrl")
if not pv and d.get("taskId"):
    for i in range(40):
        time.sleep(5)
        st = call("/api/v1/preview/status?taskId=%s" % d["taskId"], token=token)["data"]
        if st["status"] in ("ready", "failed"):
            print("  %ds -> %s %s" % ((i + 1) * 5, st["status"], st.get("errMsg", "")))
            pv = st.get("previewUrl")
            break
        if i % 4 == 0:
            print("  %ds -> %s" % ((i + 1) * 5, st["status"]))
if not pv:
    print("未拿到预览地址")
    raise SystemExit(1)

print("渲染总耗时: %.1f 秒" % (time.time() - t0))
code, final, page = get(pv, token)
print("最终地址:", final[:90])
print("预览页 HTTP %s, %s 字节" % (code, len(page)))
print("表格模式  :", "/sheet/" in final)
print("工作表数  :", page.count('class="tab'))
print("表格行数  :", page.count("<tr>"))
import urllib.parse as _up
print("含水印    :", "svg+xml" in page and
      _up.unquote("示例事业部内部资料") in _up.unquote(page))
print("含表头sticky:", "position:sticky" in page)
print("片段      :", " ".join(page[:300].split())[:260])
