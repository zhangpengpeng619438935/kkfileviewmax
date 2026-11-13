#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端验证: 老版 .ppt 从打开到内容页/图片/PDF 全链路。

用法: python verify_legacy_ppt.py <文件名>
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
NAME = sys.argv[1] if len(sys.argv) > 1 else "legacy_v3.ppt"


def post(path, payload, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(), h)
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def get(url, follow=True):
    op = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(url, timeout=300)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


tk = post("/api/v1/auth/token", {"appId": "kk-demo",
                                 "appSecret": "your-app-secret"}
          )["data"]["accessToken"]
t0 = time.time()
d = post("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/" + urllib.parse.quote(NAME),
          "empNo": "EMP0001", "empName": "验证"}, tk)["data"]
print("[1] preview/url  %-8s %.2fs" % (d.get("status"), time.time() - t0))

t1 = time.time()
st, hdr, _ = get(d["previewUrl"], follow=False)
loc = hdr.get("location") or ""
print("[2] /page        %d -> %s (%.2fs)"
      % (st, loc.split("?")[0], time.time() - t1))

t2 = time.time()
st2, _, body = get(loc if loc.startswith("http") else BASE + loc)
cost = time.time() - t2
txt = body.decode("utf-8", "replace")
print("[3] 内容页       %d  %d bytes  %.2fs" % (st2, len(body), cost))
m = re.findall(r"第 (\d+) / (\d+) 页", txt)
print("    幻灯片卡片   %d 张, 页数标记: %s" % (len(m), m[-1] if m else "无"))
media = re.findall(r'/progmedia/[^"]+', txt)
print("    内嵌图片     %d 张" % len(media[:6]))
if media:
    st3, h3, b3 = get(BASE + media[0])
    print("[4] 图片         %d  %d bytes  %s"
          % (st3, len(b3), h3.get("content-type")))

kkt = loc.split("kkt=")[1] if "kkt=" in loc else ""
fk = loc.split("/prog/")[1].split("?")[0]
for i in range(60):
    time.sleep(1)
    r = json.loads(get("%s/api/v1/preview/ready?f=%s&kkt=%s"
                       % (BASE, fk, kkt))[2])
    if r.get("ready"):
        print("[5] PDF 就绪     轮询 %ds" % (i + 1))
        break
st4, h4, _ = get("%s/prog/%s?kkt=%s" % (BASE, fk, kkt), follow=False)
print("[6] /prog 再进    %d -> %s" % (st4, (h4.get("location") or "").split("?")[0]))
pdf = get("%s/pdf/%s?kkt=%s" % (BASE, fk, kkt))[2]
pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
print("[7] PDF          %d bytes, 页数 %d" % (len(pdf), pages))
