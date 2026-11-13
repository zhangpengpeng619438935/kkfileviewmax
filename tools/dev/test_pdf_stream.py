"""复现 pdf.js 取 PDF 流的完整链路, 定位"PDF 为空"问题。

用法: python3 tools/test_pdf_stream.py [samples 下的文件名, 默认 real.pdf]
步骤:
  1. preview/url 拿预览地址 -> 打开 onlinePreview 页 -> 解析出 viewer.html 的 file 参数
  2. 按 pdf.js 的方式请求该文件流: 不带 Range / 带 Range / 带 Cookie
  3. 对比源站直连的字节数
"""
import base64
import http.cookiejar
import json
import re
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"


def call(path, payload=None, token=None, timeout=180):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


def main():
    fname = sys.argv[1] if len(sys.argv) > 1 else "real.pdf"
    src = "http://127.0.0.1:8899/" + fname

    # 源站直连的字节数(基准)
    try:
        raw = urllib.request.urlopen(src, timeout=60).read()
        print("源站直连        : %10d 字节" % len(raw))
    except Exception as exc:
        print("源站直连        : 失败 %s" % exc)
        raw = b""

    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    d = call("/api/v1/preview/url", {"fileUrl": src, "empNo": "EMP0001"}, token)["data"]
    preview = d.get("previewUrl")
    if not preview:
        print("未拿到预览地址:", d)
        return 1

    # 带 cookie 打开预览页(模拟浏览器, 让网关下发 kk_pv cookie)
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    page = op.open(urllib.request.Request(
        preview, headers={"X-App-Id": APP}), timeout=120).read().decode("utf-8", "replace")
    mode = "pdf.js" if "viewer.html" in page else ("图片" if "goForImage" in page else "?")
    print("预览页渲染模式  : %s" % mode)

    # pdf.js 真正取流的地址: 引擎页面里是
    #   url = baseUrl + 'getCorsFile?urlPath=' + encodeURIComponent(Base64.encode(src))
    file_url = ("%s/kk/getCorsFile?urlPath=%s"
                % (BASE, urllib.parse.quote(base64.b64encode(src.encode()).decode())))
    print("pdf.js 取流地址 : %s" % file_url[:96])

    # 从预览地址里取出一次性令牌, 直接挂在取流地址上(等价于预览页内发起请求)
    pv_token = urllib.parse.parse_qs(
        urllib.parse.urlparse(preview).query).get("token", [""])[0]
    authed = file_url + "&kkt=" + urllib.parse.quote(pv_token)

    def fetch(url, label, rng=None, timeout=180):
        h = {"X-App-Id": APP}
        if rng:
            h["Range"] = rng
        try:
            r = op.open(urllib.request.Request(url, headers=h), timeout=timeout)
            body = r.read()
            print("%-22s HTTP %s  %10d 字节  content-length=%-9s encoding=%-5s type=%s"
                  % (label, r.status, len(body),
                     r.headers.get("content-length", "-"),
                     r.headers.get("content-encoding", "-"),
                     (r.headers.get("content-type") or "-")[:24]))
            return len(body)
        except urllib.error.HTTPError as e:
            print("%-22s HTTP %s  %s" % (label, e.code, (e.read()[:80] or b"").decode("utf-8", "replace")))
            return 0
        except Exception as e:
            print("%-22s 失败 %s" % (label, e))
            return 0

    print()
    n1 = fetch(authed, "① 带令牌 不带 Range")
    n2 = fetch(authed, "② 带令牌 Range 0-1023", rng="bytes=0-1023")
    # 模拟 pdf.js: 显式声明接受 gzip(Starlette 的 GZipMiddleware 会介入)
    req = urllib.request.Request(authed, headers={
        "X-App-Id": APP, "Accept-Encoding": "gzip, deflate"})
    try:
        r = op.open(req, timeout=180)
        body = r.read()
        print("%-22s HTTP %s  %10d 字节  encoding=%s"
              % ("③ 带令牌+gzip", r.status, len(body),
                 r.headers.get("content-encoding", "-")))
    except urllib.error.HTTPError as e:
        print("%-22s HTTP %s %s" % ("③ 带令牌+gzip", e.code,
                                    (e.read()[:80] or b"").decode("utf-8", "replace")))
    except Exception as e:
        print("%-22s 失败 %s" % ("③ 带令牌+gzip", e))

    print()
    ok = bool(raw) and n1 == len(raw)
    print("结论:", "文件流完整(与源站一致)" if ok else
          "!!! 文件流异常: 源站 %d 字节 vs 代理 %d 字节" % (len(raw), n1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
