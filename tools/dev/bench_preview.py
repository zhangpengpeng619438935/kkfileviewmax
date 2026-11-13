#!/usr/bin/env python3
"""预览耗时基准: 分别测 preview/url(等待转码) 与页面/PDF 取流。
用法: python3 tools/bench_preview.py [label]
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def probe(token, name):
    t0 = time.time()
    try:
        d = call("/api/v1/preview/url",
                 {"fileUrl": SRC + name, "empNo": "EMP0001", "empName": "基准"}, token)["data"]
    except urllib.error.HTTPError as e:
        return {"name": name, "error": "HTTP %s %s" % (e.code, e.read().decode("utf-8", "replace")[:120])}
    api = time.time() - t0
    loc = ""
    try:
        r = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(
            d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                      "Authorization": "Bearer " + token}), timeout=180)
        loc = r.headers.get("Location", "")
        page_bytes = len(r.read())
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.headers else ""
        page_bytes = 0
    mode = ("表格" if "/sheet/" in loc else "本地PDF" if "/pdfview/" in loc
            else "引擎PDF" if "onlinePreview" in loc else "?")
    return {"name": name, "api": api, "mode": mode, "page_bytes": page_bytes,
            "fileKey": d.get("fileKey", ""), "status": d.get("status")}


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    token = call("/api/v1/auth/token",
                 {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    cases = ["real.docx", "real.pptx", "bom_test.xlsx", "vda.pptx"]
    print("== %s ==" % (label or "基准"))
    print("%-14s %-8s %8s %10s" % ("文件", "模式", "接口(s)", "页面字节"))
    for c in cases:
        r = probe(token, c)
        if "error" in r:
            print("%-14s %s" % (r["name"], r["error"]))
        else:
            print("%-14s %-8s %8.2f %10d" % (r["name"], r["mode"], r["api"], r["page_bytes"]))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
