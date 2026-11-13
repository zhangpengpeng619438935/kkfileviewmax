#!/usr/bin/env python3
"""验收: Excel 高保真渲染(样式/图片/条件格式) + 2 秒内出图。"""
import json
import re
import sys
import time
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


def panes_of(page):
    return [seg.split("</table></div>")[0] for seg in page.split('<div class="pane')[1:]]


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]

for name in ["apqp.xlsx", "bom.xlsx"]:
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    api = time.time() - t0
    t1 = time.time()
    r = urllib.request.urlopen(urllib.request.Request(
        d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                  "Authorization": "Bearer " + token}), timeout=120)
    page = r.read().decode("utf-8", "replace")
    first = time.time() - t1
    panes = panes_of(page)
    kkt = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query)["token"][0]

    print("=" * 74)
    print("%s | 接口 %.2fs | 首屏 %.2fs | %.2fMB | 工作表 %d"
          % (name, api, first, len(page) / 1048576, len(panes)))
    print("  条件格式着色单元格: %d | 浮动图片: %d | 冻结单元格: %d"
          % (len(re.findall(r"background:#", page)), page.count("xlimg"),
             page.count("position:sticky")))
    for i, seg in enumerate(panes):
        cols = seg.count('<th class="ch"')
        rows = [int(x) for x in re.findall(r'<th class="rn"[^>]*>(\d+)</th>', seg)]
        name_m = re.search(r'class="tab[^"]*" data-i="%d">([^<]+)' % i, page)
        print("   %-14s 列 %-3d 行 %-3d %s"
              % (name_m.group(1)[:12] if name_m else "?", cols, len(rows),
                 "(行号 %d~%d)" % (min(rows), max(rows)) if rows else ""))
    # 图片真的能取到?
    m = re.search(r'src="(/xlsmedia/[^"]+)"', page)
    if m:
        url = BASE + m.group(1) + "?kkt=" + urllib.parse.quote(kkt)
        ir = urllib.request.urlopen(url, timeout=60)
        print("   图片取样: HTTP %s %dKB %s"
              % (ir.status, len(ir.read()) // 1024, ir.headers.get("content-type")))
    sys.stdout.flush()
