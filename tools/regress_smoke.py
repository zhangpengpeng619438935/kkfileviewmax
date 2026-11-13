#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""改动后的冒烟回归: 各类型文件打开后的落点 + 首屏是否真有内容。

用法: python regress_smoke.py  (从本机跑, 打 127.0.0.1:9012)
"""
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"
FILES = ["real.pptx", "legacy113.ppt", "real.docx", "real.xlsx", "real.xls",
         "real.pdf",
         # 源站上这份文件的空格是 NBSP(U+00A0), 不是普通空格
         "01\u00a0\uff1aX6M\u00a0\u00a0APQP\u00a0\u00a0\u00a0周会\u00a0"
         "\uff08车厂&\u00a0示例\u00a0\uff09\uff08O\uff09---20260325.xlsx"]


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
print("%-46s %-9s %-26s %s" % ("文件", "状态", "落点", "首屏"))
print("-" * 100)
bad = 0
for name in FILES:
    try:
        d = post("/api/v1/preview/url",
                 {"fileUrl": SRC + urllib.parse.quote(name, safe=""),
                  "empNo": "EMP0001", "empName": "回归"}, tk)["data"]
        st, hdr, _ = get(d["previewUrl"], follow=False)
        loc = hdr.get("location") or ""
        st2, _, body = get(BASE + loc if loc.startswith("/") else loc, follow=True)
        path_only = loc.split("?")[0]
        if path_only.startswith("/univer"):
            # Univer 是单页应用：HTML 只是个壳，内容来自 /api/v1/univer/data。
            # 所以这里顺着令牌再拉一次快照，确认快照真的生成出来了。
            q = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
            tk2 = (q.get("kkt") or [""])[0]
            st3, _, sbody = get(BASE + "/api/v1/univer/data?kkt=" + urllib.parse.quote(tk2, safe=""),
                                follow=True)
            try:
                js = json.loads(sbody)
            except Exception:
                js = {}
            has = bool((js.get("data") or {}).get("data"))
            # 页面本身也要能打开：Univer 的外壳是网关的一条路由，
            # 曾经因为删代码时连带删掉装饰器，快照接口照常返回而页面 404，
            # 只查快照的检查完全看不出来。
            stp, _, pbody = get(BASE + loc, follow=True)
            page_ok = stp == 200 and b'id="app"' in pbody
            ok = st3 == 200 and js.get("code") == 0 and has and page_ok
            size = len(sbody)
            detail = "" if ok else (sbody[:120].decode("utf-8", "replace"))
            print("%-46s %-9s %-26s %s%s" % (name[:44], d.get("status"), path_only[:24],
                                             ("OK 快照%dKB" % (size // 1024)) if ok
                                             else ("FAIL 页面%s" % stp if not page_ok else "FAIL"),
                                             (" " + detail) if detail else ""))
            if not ok:
                bad += 1
            continue
        ok = st2 == 200 and len(body) > 2000 and b'"detail"' not in body[:200]
        detail = ""
        if not ok and b'"detail"' in body[:400]:
            detail = body[:120].decode("utf-8", "replace")
        print("%-46s %-9s %-26s %s%s" % (name[:44], d.get("status"),
                                         path_only[:24] or "(引擎)",
                                         "OK %dKB" % (len(body) // 1024) if ok else "FAIL",
                                         (" " + detail) if detail else ""))
        if not ok:
            bad += 1
    except Exception as exc:
        print("%-46s %-9s %-26s %s" % (name[:44], "ERR", "-", exc))
        bad += 1
print("-" * 100)
print("失败 %d / %d" % (bad, len(FILES)))
