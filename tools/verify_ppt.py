#!/usr/bin/env python3
"""验收: PPT 秒开内容页 → 后台转 PDF → 自动切换。"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/ehs.pptx"


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]

t0 = time.time()
d = call("/api/v1/preview/url", {"fileUrl": SRC, "empNo": "EMP0001"}, token)["data"]
print("1) preview/url: %.2fs  status=%s" % (time.time() - t0, d.get("status")))
pv = d["previewUrl"]
kkt = urllib.parse.parse_qs(urllib.parse.urlparse(pv).query).get("token", [""])[0]
fk = d["fileKey"]

t0 = time.time()
r = urllib.request.urlopen(urllib.request.Request(
    pv, headers={"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}), timeout=300)
page = r.read().decode("utf-8", "replace")
cost = time.time() - t0
slides = page.count('<section class="slide">')
print("2) 首屏打开: %.2fs  %dKB  页数=%d  落点=%s"
      % (cost, len(page) // 1024, slides, r.url.split("9012")[-1][:36]))
print("   含图片: %s | 含水印: %s" % ("/progmedia/" in page, "background-image:url" in page))

m = re.search(r"/progmedia/%s/([^\"]+)" % fk, page)
if m:
    t0 = time.time()
    ir = urllib.request.urlopen(BASE + "/progmedia/%s/%s?kkt=%s"
                                % (fk, m.group(1), urllib.parse.quote(kkt)), timeout=60)
    blob = ir.read()
    print("3) 图片: %.2fs %dKB %s" % (time.time() - t0, len(blob) // 1024,
                                     ir.headers.get("content-type")))

print("4) 等待后台 PDF …")
t0 = time.time()
for _ in range(60):
    time.sleep(2)
    try:
        r = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(
            pv, headers={"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}),
            timeout=60)
        loc = r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "") if e.headers else ""
    if "/pdfview/" in loc:
        print("   就绪: %.1fs, 已切到 %s" % (time.time() - t0, loc.split("?")[0]))
        pdfr = urllib.request.urlopen(urllib.request.Request(
            BASE + loc, headers={"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}),
            timeout=120)
        print("   最终页: %s | %dKB" % (pdfr.url.split("9012")[-1][:44], len(pdfr.read()) // 1024))
        break
else:
    print("   超时仍未就绪")
    sys.exit(1)
