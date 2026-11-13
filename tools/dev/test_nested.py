# -*- coding: utf-8 -*-
"""嵌套压缩包 + 返回上级 的验证：外层 zip 里的 inner.zip 能否继续点开。"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, json.dumps(payload).encode() if payload else None, h)
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def fetch(u):
    op = urllib.request.build_opener(NR)
    try:
        r = op.open(u, timeout=180)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def main():
    url = sys.argv[1]
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    d = call("/api/v1/preview/url", {"fileUrl": url, "empNo": "EMP0001", "empName": "v"}, tk)["data"]
    kkt = urllib.parse.parse_qs(urllib.parse.urlparse(d["previewUrl"]).query)["token"][0]
    fk = d["fileKey"]
    qkk = urllib.parse.quote(kkt, safe="")
    tgt = "包中包/inner.zip"

    st, hdr, _ = fetch("%s/archive/%s/view?p=%s&kkt=%s"
                       % (BASE, fk, urllib.parse.quote(tgt), qkk))
    loc = hdr.get("location", "")
    print("进入内层 %s -> %s %s" % (st, loc.replace(BASE, "")[:70], ""))
    st, hdr, body = fetch(loc if loc.startswith("http") else BASE + loc)
    html = body.decode("utf-8", "replace")
    print("内层清单 %s  %d bytes  返回上级=%s  含 hello=%s"
          % (st, len(body), "返回上级" in html, "hello.txt" in html))
    chain = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("chain", [""])[0]
    inner = "深处/hello.txt"
    st, hdr, body = fetch("%s/archive/%s/view?p=%s&chain=%s&kkt=%s"
                          % (BASE, fk, urllib.parse.quote(inner), urllib.parse.quote(chain), qkk))
    html = body.decode("utf-8", "replace")
    print("内层文件 %s  %s  %d bytes  含正文=%s"
          % (st, hdr.get("content-type", "").split(";")[0], len(body), "我来自内层压缩包" in html))
    print("完成")


if __name__ == "__main__":
    main()
