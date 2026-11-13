#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证: 1) 含裸 & 的签名链接能否被网关自动修复并转码  2) md 文件是否支持预览"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:9012"
APP_ID = "kk-demo"
SECRET = "your-app-secret"

RAW = ("http://127.0.0.1:8080/api/file/raw/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "?path=1-1%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/01%20%E6%8A%80%E6%9C%AF%E6%96%87%E4%BB%B6/"
       "07%20%E4%BE%9B%E5%BA%94%E5%95%86PPAP%E8%B5%84%E6%96%99/C-HUD-W024A%20%20%E4%B8%9C%E9%A3%8E"
       "%E4%B9%98%E7%94%A8%E8%BD%A6S73A%20PPAP/%E5%BB%B8%E7%94%9F%E5%A1%91%E8%83%B6%20%20"
       "%E8%9C%97%E6%9D%86%203110314000169/11-%E5%88%9D%E5%A7%8B%E8%BF%87%E7%A8%8B%E7%A0%94%E7%A9%B6"
       "%EF%BC%88Cpk%20&%20Ppk%EF%BC%89/3110314000169-%E8%9C%97%E6%9D%86-PPK.xls"
       "&exp=1792489834&sig=ea3104dc50181c4d08554882d8e33123")


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
        return r.status, r.read().decode("utf-8", "replace")


token = call("/api/v1/auth/token", {"appId": APP_ID, "appSecret": SECRET})["data"]["accessToken"]

print("=== A. 含裸 & 的 xls 签名链接 ===")
res = call("/api/v1/preview/url", {"fileUrl": RAW, "empNo": "EMP0001",
                                   "empName": "验证", "dept": "示例事业部"}, token)
d = res.get("data", {})
print("  code=%s status=%s urlFixed=%s" % (res.get("code"), d.get("status"), d.get("urlFixed")))
if "detail" in res:
    print("  错误: %s" % res["detail"][:300])
else:
    print("  规范化后地址尾部: ...%s" % str(d.get("fileUrl"))[-70:])
    pv = d.get("previewUrl")
    if not pv and d.get("taskId"):
        for i in range(20):
            time.sleep(3)
            st = call("/api/v1/preview/status?taskId=%s" % d["taskId"], token=token)["data"]
            print("  %2ds 状态=%s" % ((i + 1) * 3, st["status"]), end="\r")
            if st["status"] == "ready":
                pv = st.get("previewUrl")
                break
            if st["status"] == "failed":
                print("\n  转码失败: %s" % st.get("errMsg"))
                break
    print()
    if pv:
        code, html = get(pv, token)
        print("  预览页 HTTP %s, %s 字节" % (code, len(html)))
        print("  页面特征: %s" % ", ".join(
            k for k in ("viewer.html", "pdf.js", "iframe", "notSupported",
                        "暂不支持", "imagePreview") if k in html) or "无")
        print("  页面片段: %s" % " ".join(html[:700].split())[:700])

print("\n=== B. md 文件预览 ===")
res2 = call("/api/v1/preview/url",
            {"fileUrl": "http://127.0.0.1:8899/test.md",
             "empNo": "EMP0001", "empName": "验证"}, token)
d2 = res2.get("data", {})
print("  status=%s fileType=%s" % (d2.get("status"), d2.get("fileType")))
if d2.get("previewUrl"):
    code, html = get(d2["previewUrl"], token)
    print("  预览页 HTTP %s, %s 字节" % (code, len(html)))
    print("  页面含 md 正文: %s" % ("示例数字化平台" in html))
    print("  文件类型判断: %s" % ("文本类直接渲染" if "text" in html or "markdown" in html.lower()
                                else "见页面内容"))
