# -*- coding: utf-8 -*-
"""压缩包更新的可见性：包里同名文件被替换后，点开条目能否看到新内容。

用法: python3 tools/test_archive_ttl.py   （需 samples 可写，源站 8899 可访问）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"
NAME = "t_ttl.zip"
PATH = "/opt/kkfileview-service/samples/" + NAME


def make(text):
    with zipfile.ZipFile(PATH, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.txt", text.encode("utf-8"))


def call(path, payload=None, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode() if payload else None, h)
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def fetch(u):
    try:
        return urllib.request.urlopen(u, timeout=120).read()
    except urllib.error.HTTPError as e:
        return b"HTTP %d %s" % (e.code, e.read()[:120])


def open_entry(fk, kkt):
    u = "%s/archive/%s/view?p=%s&kkt=%s" % (
        BASE, fk, urllib.parse.quote("a.txt"), urllib.parse.quote(kkt, safe=""))
    return fetch(u).decode("utf-8", "replace")


def body_of(html):
    if html[:5] == "HTTP ":
        return "ERR " + html[:60].replace("\n", " ")
    for t in ("v2", "v1"):
        if ">" + t + "<" in html:
            return t
    return "?"


def main():
    make("v1")
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    d = call("/api/v1/preview/url", {"fileUrl": SRC + NAME, "empNo": "EMP0001"}, tk)["data"]
    fk = d["fileKey"]
    kkt = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query)["token"][0]
    print("fk:", fk)
    print("首次:", body_of(open_entry(fk, kkt)))

    # 换成等长内容（v1 -> v2）：验证条目 PDF/文本不会因长度相同而命中旧结果
    make("v2")
    print("替换后立即:", body_of(open_entry(fk, kkt)), "（TTL 内应是旧值）")

    print("等 65 秒让缓存过期…")
    time.sleep(65)
    print("过期后:", body_of(open_entry(fk, kkt)), "（应变成 v2）")
    print("保留测试包:", PATH)


if __name__ == "__main__":
    sys.exit(main())
