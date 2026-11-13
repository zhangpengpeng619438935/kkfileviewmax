#!/usr/bin/env python3
"""端到端耗时基准: 接口等待 + 实际 PDF 首字节(真正的"打开要多久")。
在宿主机跑, 会先清空引擎缓存, 保证是冷转换。
"""
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"
CACHE = "/opt/kkfileview-service/data/kkfileview"


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    headers = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=headers),
        timeout=timeout).read())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def clean_cache():
    for p in glob.glob(CACHE + "/*"):
        try:
            os.remove(p)
        except Exception:
            pass
    for d in glob.glob("/opt/kkfileview-service/data/gateway/sheets/*"):
        try:
            os.remove(d)
        except Exception:
            pass


def clear_state():
    os.system("docker exec kk-redis redis-cli -n 3 flushdb >/dev/null 2>&1")
    os.system("docker exec kk-mysql mysql -uroot -pchange-me-root-password kkfileview "
              "-e 'DELETE FROM preview_task' >/dev/null 2>&1")
    clean_cache()


def run(token, name):
    t0 = time.time()
    try:
        d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    except urllib.error.HTTPError as e:
        return "%s HTTP %s %s" % (name, e.code, e.read().decode("utf-8", "replace")[:80])
    api = time.time() - t0
    # 拿页面, 解析真正的取流地址
    try:
        r = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(
            d["previewUrl"], headers={"X-App-Id": "kk-demo",
                                      "Authorization": "Bearer " + token}), timeout=180)
    except urllib.error.HTTPError as e:
        r = e
    loc = (r.headers.get("Location", "") if r.headers else "")
    if "/pdfview/" in loc:
        fk = d["fileKey"]
        tk = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query).get("token", [""])[0]
        pdf_url = BASE + "/pdf/" + fk + "?kkt=" + urllib.parse.quote(tk)
        kind = "本地PDF"
    elif "onlinePreview" in loc:
        page = urllib.request.urlopen(urllib.request.Request(
            BASE + loc, headers={"X-App-Id": "kk-demo",
                                 "Authorization": "Bearer " + token}), timeout=180).read().decode(
            "utf-8", "replace")
        m = re.search(r"var url = '([^']+)'", page)
        if not m:
            return "%-14s %-7s 接口 %.2fs | 解析不到取流地址" % (name, "引擎PDF", api)
        pdf_url = m.group(1)
        kind = "引擎PDF"
    else:
        return "%-14s %-7s 接口 %.2fs | 未知落点 %s" % (name, "?", api, loc[:40])

    # 取流: 首字节 = 用户看到第一页的时间
    t1 = time.time()
    try:
        pr = urllib.request.urlopen(pdf_url, timeout=300)
        first = pr.read(1024)
        t_first = time.time() - t1
        rest = pr.read()
        total = len(first) + len(rest)
    except urllib.error.HTTPError as e:
        return "%-14s %-7s 接口 %.2fs | PDF HTTP %s" % (name, kind, api, e.code)
    return ("%-14s %-7s 接口 %5.2fs | 首字节 %5.2fs | 总 %5.2fs | %6.1fKB | 魔数 %s"
            % (name, kind, api, t_first, time.time() - t1, total / 1024.0, first[:5]))


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    token = call("/api/v1/auth/token",
                 {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    names = sys.argv[2:] or ["real.docx", "real.pptx", "bom_test.xlsx", "real.xlsx", "vda.pptx"]
    print("== %s ==" % (label or "基准"))
    for n in names:
        clear_state()
        print(run(token, n))
        sys.stdout.flush()


if __name__ == "__main__":
    main()
