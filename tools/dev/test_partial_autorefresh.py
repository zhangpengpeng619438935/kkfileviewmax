#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证「PPT 部分版 → 完整版」自动切换:

1. 冷打开一个 >30 页的 pptx（两阶段转换）
2. 部分版就绪后抓 pdf.js 页面，确认注入了轮询脚本（kkpart）
3. 轮询 /api/v1/preview/partial，确认"部分版 → 完整版"时
   partial 变为空、ver 发生变化（= 前端会自动 reload 的条件）

用法: python test_partial_autorefresh.py [文件名]
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
name = sys.argv[1] if len(sys.argv) > 1 else "ehs_v2.pptx"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def post(path, payload, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode(), h)
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


def get(url, follow=False):
    op = urllib.request.build_opener() if follow else \
        urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(url, timeout=300)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


tk = post("/api/v1/auth/token",
          {"appId": "kk-demo", "appSecret": "your-app-secret"}
          )["data"]["accessToken"]
t0 = time.time()
d = post("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/" + urllib.parse.quote(name, safe=""),
          "empNo": "EMP0001", "empName": "两阶段验证"}, tk)["data"]
fk = d["fileKey"]
print("[1] preview/url  %s provisional=%s %.2fs"
      % (d.get("status"), d.get("provisional"), time.time() - t0))
token = d["previewUrl"].split("token=")[1]
kkt = urllib.parse.quote(token, safe="")

# 等部分版就绪（partial 标记出现 + PDF 可读）
part, view_html, ver_partial = "", "", 0
for i in range(120):
    time.sleep(1.5)
    st, _, body = get("%s/api/v1/preview/partial?f=%s&kkt=%s" % (BASE, fk, kkt))
    if st != 200:
        print("    partial 接口异常:", st, body[:120])
        break
    j = json.loads(body)
    if j["partial"]:
        part, ver_partial = j["partial"], j["ver"]
        print("[2] 部分版就绪 %.1fs  partial=%s ver=%s ready=%s"
              % (time.time() - t0, part, j["ver"], j["ready"]))
        # 抓 pdf.js 页面（走真实跳转链）
        st1, h1, _ = get("%s/api/v1/preview/page?token=%s" % (BASE, token))
        loc = h1.get("location", "")
        st2, h2, _ = get(BASE + loc if loc.startswith("/") else loc)
        loc2 = h2.get("location", "")
        st3, _, page = get(BASE + loc2 if loc2.startswith("/") else loc2)
        view_html = page.decode("utf-8", "replace")
        print("[3] viewer 页面 %d, 含进度横幅=%s, 含自动轮询=%s"
              % (st3, "kkpart" in view_html,
                 "/api/v1/preview/partial" in view_html))
        break

if not part:
    print("!! 没等到部分版标记，可能文件页数不足 30 或走了单阶段")
    sys.exit(1)

m = re.search(r"var fk=(.+?), kkt=(.+?), ver=(\d+)", view_html)
print("    注入参数 fk/kkt/ver =", (m.group(1)[:14] + "…") if m else "未找到",
      (m.group(3) if m else ""))

for i in range(240):
    time.sleep(2)
    j = json.loads(get("%s/api/v1/preview/partial?f=%s&kkt=%s"
                       % (BASE, fk, kkt))[2])
    if not j["partial"]:
        print("[4] 完整版就绪 %.1fs  partial='' ver=%s（部分版时为 %s，%s）"
              % (time.time() - t0, j["ver"], ver_partial,
                 "版本已变化→页面会自动刷新" if j["ver"] != ver_partial
                 else "版本相同→不会刷新(异常)"))
        break
else:
    print("!! 180 秒内完整版未就绪")
