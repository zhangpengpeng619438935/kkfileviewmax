"""回归验证: 注入类页面/文件流的 content-length 与实际字节是否一致。

用法: python3 tools/regression_check.py
覆盖:
  1. onlinePreview 预览页(注入白底+水印后) content-length == 实际字节
  2. pdf.js viewer 页(注入后) content-length == 实际字节
  3. /kk/getCorsFile 文件流字节数 == 源站文件字节数
  4. 表格模式页正常返回
  5. 空文件在接口层被拦(400)
"""
import base64
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"
SAMPLES = "http://127.0.0.1:8899/"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(path, payload=None, token=None, timeout=180):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


def main():
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %-40s %s" % ("OK" if cond else "!!", label, extra))

    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def get(url, headers=None):
        h = {"X-App-Id": APP}
        h.update(headers or {})
        return op.open(urllib.request.Request(url, headers=h), timeout=180)

    print("=== 1. PDF 预览页(注入后) ===")
    d = call("/api/v1/preview/url", {"fileUrl": SAMPLES + "real.pdf", "empNo": "EMP0001"},
             token)["data"]
    r = get(d["previewUrl"])
    body = r.read()
    cl = r.headers.get("content-length")
    check("content-length 与实际字节一致",
          cl is not None and int(cl) == len(body),
          "header=%s 实际=%d" % (cl, len(body)))
    check("页面含 pdf.js viewer 引用", b"viewer.html" in body)
    kkt = urllib.parse.parse_qs(
        urllib.parse.urlparse(d["previewUrl"]).query).get("token", [""])[0]

    print()
    print("=== 2. pdf.js viewer 页(注入后) ===")
    vr = get(BASE + "/kk/pdfjs/web/viewer.html")
    vbody = vr.read()
    vcl = vr.headers.get("content-length")
    check("content-length 与实际字节一致",
          vcl is not None and int(vcl) == len(vbody),
          "header=%s 实际=%d" % (vcl, len(vbody)))
    check("注入白底生效", b"--body-bg-color:#fff" in vbody)
    check("注入水印生效", b"background-image:url(\"data:image/svg+xml" in vbody)
    check("gzip 未作用于 HTML(或已重算)", vcl is not None,
          "encoding=%s" % vr.headers.get("content-encoding", "-"))

    print()
    print("=== 3. 文件流字节数 ===")
    src_len = len(urllib.request.urlopen(SAMPLES + "real.pdf", timeout=120).read())
    fu = ("%s/kk/getCorsFile?urlPath=%s&kkt=%s"
          % (BASE, urllib.parse.quote(base64.b64encode((SAMPLES + "real.pdf").encode()).decode()),
             urllib.parse.quote(kkt)))
    fr = get(fu)
    fb = fr.read()
    check("文件流完整", len(fb) == src_len,
          "源站=%d 代理=%d" % (src_len, len(fb)))
    check("文件流未被 gzip", fr.headers.get("content-encoding") in (None, "identity"),
          "encoding=%s" % fr.headers.get("content-encoding", "-"))

    print()
    print("=== 4. 表格模式页 ===")
    d2 = call("/api/v1/preview/url", {"fileUrl": SAMPLES + "bom_test.xlsx", "empNo": "EMP0001"},
              token)["data"]
    tgt, code = "", 0
    try:
        r2 = urllib.request.build_opener(NoRedirect).open(urllib.request.Request(
            d2["previewUrl"], headers={"X-App-Id": APP}), timeout=180)
        tgt, code = r2.headers.get("Location", ""), r2.status
    except urllib.error.HTTPError as e:
        tgt, code = (e.headers.get("Location", "") if e.headers else ""), e.code
    check("走表格模式(/sheet/)", "/sheet/" in tgt, tgt[:60])
    if "/sheet/" in tgt:
        sp = BASE + tgt
        sr = get(sp)
        sb = sr.read()
        scl = sr.headers.get("content-length")
        check("表格页 content-length 一致",
              scl is not None and int(scl) == len(sb),
              "header=%s 实际=%d" % (scl, len(sb)))

    print()
    print("=== 5. 空文件拦截 ===")
    try:
        call("/api/v1/preview/url", {"fileUrl": SAMPLES + "empty.pdf", "empNo": "EMP0001"}, token)
        check("空文件被拦", False, "却返回了成功")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        check("空文件被拦(400)", e.code == 400 and "0 字节" in txt, "HTTP %d" % e.code)

    print()
    print("结论:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
