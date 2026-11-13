#!/usr/bin/env python3
"""最终回归: 各类型文件的落点、冷/热耗时、文件流有效性。"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


def clean():
    os.system("docker exec kk-redis redis-cli -n 3 flushdb >/dev/null 2>&1")
    os.system("docker exec kk-mysql mysql -uroot -pchange-me-root-password kkfileview "
              "-e 'DELETE FROM preview_task' >/dev/null 2>&1")
    for d in ("/opt/kkfileview-service/data/gateway/sheets",
              "/opt/kkfileview-service/data/kkfileview"):
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
            except Exception:
                pass


def open_one(token, name):
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    cost = time.time() - t0
    r = urllib.request.urlopen(urllib.request.Request(
        d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                  "Authorization": "Bearer " + token}), timeout=180)
    final = r.url
    page = r.read()
    if "/pdfjs/web/viewer.html" in final:
        kind = "pdf.js查看器"
        f = urllib.parse.parse_qs(urllib.parse.urlparse(final).query).get("file", [""])[0]
        pr = urllib.request.urlopen(f, timeout=180)
        body = pr.read()
        valid = body[:5] == b"%PDF-"
        extra = "PDF %dKB %s" % (len(body) // 1024, "OK" if valid else "损坏")
    elif "/sheet/" in final:
        kind = "表格组件"
        extra = "%dKB HTML" % (len(page) // 1024)
    elif "PDF预览" in page.decode("utf-8", "replace"):
        kind = "引擎PDF页"
        extra = "%dKB shell" % (len(page) // 1024)
    elif "图片预览" in page.decode("utf-8", "replace"):
        kind = "图片预览"
        extra = "%dKB" % (len(page) // 1024)
    else:
        kind = "其他"
        extra = "%dKB" % (len(page) // 1024)
    return cost, kind, extra


def main():
    token = call("/api/v1/auth/token",
                 {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    cases = ["real.docx", "real.pptx", "bom_test.xlsx", "real.xlsx", "vda.pptx",
             "real.pdf", "test.png", "bom_test.csv"]
    clean()
    print("%-14s %-14s %8s %8s  %s" % ("文件", "落点", "冷(s)", "热(s)", "产物"))
    print("-" * 68)
    for c in cases:
        try:
            cold, kind, extra = open_one(token, c)
            hot, kind2, _ = open_one(token, c)
            print("%-14s %-14s %8.2f %8.2f  %s" % (c, kind, cold, hot, extra))
        except urllib.error.HTTPError as e:
            print("%-14s HTTP %s %s" % (c, e.code, e.read().decode("utf-8", "replace")[:60]))
        except Exception as e:
            print("%-14s 失败 %s" % (c, e))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
