# -*- coding: utf-8 -*-
"""压缩包内文件预览的端到端验证。

用法: python3 tools/test_archive.py <fileUrl> [条目名...]
不传条目名时用内置的测试清单（需要 t_mix.zip 这种混合内容包）。
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"


def call(path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": "kk-demo"}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=300).read())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def fetch(url):
    op = urllib.request.build_opener(NoRedirect)
    try:
        r = op.open(url, timeout=180)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()


def main():
    url = sys.argv[1]
    entries = sys.argv[2:]
    tk = call("/api/v1/auth/token",
              {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]
    d = call("/api/v1/preview/url",
             {"fileUrl": url, "empNo": "EMP0001", "empName": "验证"}, tk)["data"]
    pv = d["previewUrl"]
    kkt = urllib.parse.parse_qs(urllib.parse.urlparse(pv).query)["token"][0]
    fk = d["fileKey"]
    print("preview/url status=%s fk=%s" % (d.get("status"), fk[:12]))

    st, hdr, body = fetch(pv)
    hop = 0
    while st in (301, 302, 303, 307, 308) and hop < 4:
        loc = hdr.get("location", "")
        loc = loc if loc.startswith("http") else BASE + loc
        print("  跳转 %s -> %s" % (st, loc.replace(BASE, "")[:70]))
        st, hdr, body = fetch(loc)
        hop += 1
    print("清单页 %s  %s bytes  ct=%s" % (st, len(body), hdr.get("content-type", "")[:40]))
    txt = body.decode("utf-8", "replace")
    print("  含搜索框:", 'id="q"' in txt, " 可点条目:", txt.count('data-p=') > 0,
          " 表格行:", txt.count("<tr"))

    if not entries:
        for line in txt.split("<tr")[1:8]:
            if 'data-name="' in line:
                n = line.split('data-name="', 1)[1].split('"', 1)[0]
                entries.append(urllib.parse.unquote(n).replace("&amp;", "&"))

    qkk = urllib.parse.quote(kkt, safe="")
    for name in entries:
        u = "%s/archive/%s/view?p=%s&kkt=%s" % (BASE, fk, urllib.parse.quote(name), qkk)
        st, hdr, data = fetch(u)
        ct = hdr.get("content-type", "")
        extra = ""
        if st in (301, 302, 303, 307, 308):
            extra = " -> " + hdr.get("location", "")[:60]
        print("  %-28s %s %6d bytes  %s%s"
              % (name[:28], st, len(data), ct.split(";")[0], extra))
        if "text/html" in ct:
            t = data.decode("utf-8", "replace")
            for kw in ("下载原文件", "返回上级", "暂不支持"):
                if kw in t:
                    print("      页面含:", kw)


if __name__ == "__main__":
    main()
