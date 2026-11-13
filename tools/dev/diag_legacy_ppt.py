#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现/验证: 老版二进制 .ppt 的快速内容页(/prog)能否生成。

用法: python diag_legacy_ppt.py [文件名]
"""
import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
NAME = sys.argv[1] if len(sys.argv) > 1 else "legacy113.ppt"


def post(path, payload, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(), h)
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


def get(url, redirect=True):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    opener = urllib.request.build_opener() if redirect else \
        urllib.request.build_opener(NoRedirect)
    try:
        r = opener.open(url, timeout=300)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


tk = post("/api/v1/auth/token",
          {"appId": "kk-demo", "appSecret": "your-app-secret"}
          )["data"]["accessToken"]
d = post("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/" + urllib.parse.quote(NAME),
          "empNo": "EMP0001", "empName": "验证"}, tk)["data"]
print("status :", d.get("status"))
print("mode   :", d.get("mode"), "partial:", d.get("partial"))
url = d["previewUrl"]
print("preview:", url)

st, hdr, body = get(url, redirect=False)
print("page   :", st, "->", hdr.get("location"))
loc = hdr.get("location") or ""
if loc:
    st2, _, b2 = get(loc if loc.startswith("http") else BASE + loc)
    txt = b2.decode("utf-8", "replace")
    print("target :", st2, "len=%d" % len(b2))
    print("head   :", txt[:300].replace("\n", " "))
