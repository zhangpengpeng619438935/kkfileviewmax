# -*- coding: utf-8 -*-
"""验证「按 Excel 原样 / 全部展开」两种模式（直接打 /sheet 页）。"""
import json
import re
import time
import urllib.parse
import urllib.request

B = "http://127.0.0.1:9012"


def post(path, payload, token=None):
    h = {"Content-Type": "application/json"}
    if token:
        h.update({"X-App-Id": "kk-demo", "Authorization": "Bearer " + token})
    req = urllib.request.Request(B + path, data=json.dumps(payload).encode(), headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=180).read())


tok = post("/api/v1/auth/token",
           {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
d = post("/api/v1/preview/url",
         {"fileUrl": "http://127.0.0.1:8899/jswj.xlsx", "empNo": "C0000002"}, tok)["data"]
fk = d["fileKey"]
kkt = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query)["token"][0]
H = {"X-App-Id": "kk-demo", "Authorization": "Bearer " + tok}
base = B + "/sheet/" + fk + "?kkt=" + urllib.parse.quote(kkt, safe="")


def fetch(url):
    r = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=180)
    return r.read().decode("utf-8", "replace")


def report(page, tag):
    print("  %s" % tag)
    m = re.search(r'<span class="note">([^<]*)</span>', page)
    print("    标题栏:", (m.group(1) if m else "(空)")[:80])
    m = re.search(r'<a class="tbtn"[^>]*>([^<]+)</a>', page)
    print("    按钮:", m.group(1) if m else "(无)")
    first = re.search(r'<div class="pane" data-i="0">(.*?)</tbody>', page, re.S)
    body = first.group(1) if first else ""
    rows = re.findall(r'<th class="rn"[^>]*>(\d+)</th>', body)
    print("    汇总表: %d 行, 前 10 个行号 %s" % (len(rows), rows[:10]))
    print("    冻结(sticky):", "position:sticky;top:" in body)
    print("    页面: %.2fMB" % (len(page) / 1048576))


print("=== 默认（按 Excel 原样） ===")
p1 = fetch(base)
report(p1, "")

print()
print("=== 展开（x=1） ===")
t0 = time.time()
p2 = fetch(base + "&x=1")
print("    首次生成耗时 %.2fs" % (time.time() - t0))
report(p2, "")

print()
print("=== 展开页再次访问（应命中缓存） ===")
t0 = time.time()
fetch(base + "&x=1")
print("    %.2fs" % (time.time() - t0))

print()
print("=== 行数对比 ===")
print("    默认 %d 行 / 展开 %d 行" % (len(re.findall(r'<th class="rn"', p1)),
                                     len(re.findall(r'<th class="rn"', p2))))
print("LINK=" + base)
