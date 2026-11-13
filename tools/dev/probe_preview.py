# -*- coding: utf-8 -*-
"""端到端看预览页落点与首屏文本（验证用）。

用法: python3 tools/probe_preview.py "<fileUrl>" [显示名]
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=300).read())
    except urllib.error.HTTPError as exc:
        return {"httpError": exc.code,
                "body": exc.read().decode("utf-8", "replace")[:400]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(NoRedirect)


def fetch(url):
    try:
        r = opener.open(url, timeout=120)
        return r.status, r.headers.get("location", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("location", "") if exc.headers else ""
        return exc.code, loc, exc.read().decode("utf-8", "replace")


def main():
    url = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    t0 = time.time()
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    r = call("/api/v1/preview/url",
             {"fileUrl": url, "empNo": "EMP0001", "empName": "验证",
              "fileName": name}, tk)
    t1 = time.time()
    if r.get("code") != 0:
        print("preview/url 失败 %.2fs %s" % (t1 - t0, json.dumps(r, ensure_ascii=False)[:300]))
        return 1
    d = r["data"]
    print("preview/url %.2fs status=%s provisional=%s" % (t1 - t0, d.get("status"),
                                                          d.get("provisional")))
    st, loc, body = fetch(d["previewUrl"])
    t2 = time.time()
    print("跳转 %s -> %s  (%.2fs)" % (st, (loc or "")[:80], t2 - t1))
    if loc:
        st2, loc2, body = fetch(loc if loc.startswith("http") else BASE + loc)
        print("落点 %s -> %s" % (st2, (loc2 or "")[:80]))
    import re
    txt = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    txt = re.sub(r"<style.*?</style>", " ", txt, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    print("正文:", txt[:200])
    print("总耗时 %.2fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
