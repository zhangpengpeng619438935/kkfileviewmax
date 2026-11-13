#!/usr/bin/env python3
"""跟踪单个文件的完整预览链路: preview/url -> 页面 -> 实际文件流。"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"


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


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "real.docx"
    token = call("/api/v1/auth/token",
                 {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    t0 = time.time()
    d = call("/api/v1/preview/url", {"fileUrl": SRC + name, "empNo": "EMP0001"}, token)["data"]
    print("接口耗时 %.2fs | 状态 %s" % (time.time() - t0, d.get("status")))
    pv = d["previewUrl"]
    print("previewUrl:", pv[:110])
    try:
        r = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(
            pv, headers={"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}), timeout=180)
        print("页面 HTTP", r.status, "| Location:", r.headers.get("Location", "(无)")[:120])
    except urllib.error.HTTPError as e:
        print("页面 HTTP", e.code, "| Location:", (e.headers.get("Location") if e.headers else "")[:120])
    # 直接看网关返回的最终页
    r2 = urllib.request.urlopen(urllib.request.Request(
        pv, headers={"X-App-Id": "kk-demo", "Authorization": "Bearer " + token}), timeout=180)
    page = r2.read().decode("utf-8", "replace")
    print("最终页:", r2.url[:150])
    print("页面字节:", len(page), "| iframe:", "<iframe" in page,
          "| pdf.js viewer:", "pdfjs/web/viewer.html" in r2.url)

    # 最终页本身就是 pdf.js viewer 时, file 参数在 URL 上
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r2.url).query)
    if q.get("file"):
        f = q["file"][0]
        print("pdf.js file 参数:", f[:140])
        pr = urllib.request.urlopen(f, timeout=120)
        body = pr.read()
        print("文件流 HTTP %s | %d 字节 | 魔数 %s | content-type %s"
              % (pr.status, len(body), body[:8], pr.headers.get("content-type")))

    # 提取 pdf.js 的 file 参数
    m = re.search(r"viewer\.html\?file=([^&\"']+)", page)
    if m:
        f = urllib.parse.unquote(m.group(1))
        print("pdf.js file 参数:", f[:140])
        try:
            pr = urllib.request.urlopen(f, timeout=120)
            body = pr.read()
            print("文件流 HTTP %s | %d 字节 | 魔数 %s" % (pr.status, len(body), body[:8]))
        except urllib.error.HTTPError as e:
            print("文件流 HTTP", e.code, e.read()[:120])
        except Exception as e:
            print("文件流失败:", e)
    else:
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)).strip()
        print("正文摘要:", body[:300])
        print("---- 原始 HTML 片段 ----")
        print(page[:1800])


if __name__ == "__main__":
    main()
