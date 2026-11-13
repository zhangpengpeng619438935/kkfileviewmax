# -*- coding: utf-8 -*-
"""按业务文件地址换预览链接（验证用）。

用法: python3 tools/link_for.py "<fileUrl>" [显示名]
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=300).read())
    except urllib.error.HTTPError as exc:
        return {"httpError": exc.code, "body": exc.read().decode("utf-8", "replace")[:400]}


def main():
    url = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    print(json.dumps(call("/api/v1/preview/url",
                          {"fileUrl": url, "empNo": "EMP0001",
                           "empName": "验证", "fileName": name}, tk),
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
