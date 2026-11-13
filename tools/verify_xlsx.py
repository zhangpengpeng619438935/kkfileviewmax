#!/usr/bin/env python3
"""验收: xlsx 渲染是否完整(全行列)且够快。"""
import json
import re
import sys
import time
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
    """按 pane 切分, 返回 [(隐藏?, html片段)]。"""
    out = []
    for seg in page.split('<div class="pane')[1:]:
        body = seg.split("</table></div>")[0]
        out.append(body)
    return out


token = call("/api/v1/auth/token",
             {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]

CASES = [
    ("apqp.xlsx", [
        ("里程碑计划", 1, 138, 62, []),
        ("APQP计划", 2, 29, 101, ["版本：V1.0", "更新时间：2026/1/14"]),
        ("问题清单", 4, 16, 206, []),
    ]),
    ("bom.xlsx", [
        ("总成BOM", 0, 16, 97, []),
        ("变更履历", 1, 11, 147, []),
    ]),
]

for name, checks in CASES:
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    api = time.time() - t0
    r = urllib.request.urlopen(urllib.request.Request(
        d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                  "Authorization": "Bearer " + token}), timeout=180)
    page = r.read().decode("utf-8", "replace")
    panes = panes_of(page)
    print("=" * 72)
    print("%s | 接口 %.2fs | 页面 %.2fMB | 工作表 %d 个 | %s"
          % (name, api, len(page) / 1048576, len(panes), "表格页" if "/sheet/" in r.url else r.url[-20:]))
    for title, idx, min_cols, min_rows, must in checks:
        if idx >= len(panes):
            print("   !! 缺 pane %d (%s)" % (idx, title))
            continue
        seg = panes[idx]
        cols = seg.count('<th class="ch">')
        rows = [int(x) for x in re.findall(r'<th class="rn">(\d+)</th>', seg)]
        maxrow = max(rows) if rows else 0
        missing = [s for s in must if s not in seg]
        ok = cols >= min_cols and maxrow >= min_rows and not missing
        print("   [%s] %-14s 列 %-3d(>=%d) 行 %-3d(>=%d)%s"
              % ("OK" if ok else "!!", title, cols, min_cols, maxrow, min_rows,
                 "" if not missing else " 缺内容:%s" % missing))
    print("   水印: %s" % ("background-image:url" in page))
    sys.stdout.flush()
