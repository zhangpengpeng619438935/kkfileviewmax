#!/usr/bin/env python3
"""生成可直接点开的预览链接(仅用于验证)。用法: python3 tools/get_link.py vda.pptx"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9012"
name = sys.argv[1] if len(sys.argv) > 1 else "vda.pptx"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=300).read())


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
d = call("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/" + name, "empNo": "EMP0001",
          "empName": "验证"}, token)["data"]
print("状态:", d.get("status"))
print("预览链接:", d["previewUrl"])
