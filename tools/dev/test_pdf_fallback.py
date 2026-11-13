"""验证表格模式的 PDF 降级链路。

用法: python3 tools/test_pdf_fallback.py [samples 下的文件名]
流程:
  1. 取表格模式预览地址 -> 检查页面含 PDF 降级入口
  2. 访问 ?pdf=1&fb=1  -> 应重定向到引擎 /kk/onlinePreview
  3. 跟进引擎页面      -> 应拿到真实 PDF 预览页(不是"暂不支持")
  4. 再取一次预览地址  -> 因记忆机制应直接走 PDF(不再回表格页)
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:9012"
APP, SECRET = "kk-demo", "your-app-secret"


def call(path, payload=None, token=None, timeout=180):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, headers=h)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def get(url, token=None, allow_redirect=True, timeout=180):
    h = {"X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(*([] if allow_redirect else [NoRedirect]))
    req = urllib.request.Request(url, headers=h)
    return opener.open(req, timeout=timeout)


def main():
    fname = sys.argv[1] if len(sys.argv) > 1 else "bom_test.xlsx"
    file_url = "http://127.0.0.1:8899/" + fname
    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    ok = True

    def check(label, cond, extra=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("  [%s] %s %s" % ("OK" if cond else "!!", label, extra))

    print("=== 1. 表格模式预览页 ===")
    d = call("/api/v1/preview/url",
             {"fileUrl": file_url, "empNo": "EMP0001"}, token)["data"]
    pv = d.get("previewUrl")
    print("  状态 %s  地址 %s" % (d.get("status"), (pv or "")[:72]))
    r = get(pv, token)
    page = r.read().decode("utf-8", "replace")
    final = r.url
    check("页面返回 200", r.status == 200, "%d 字节" % len(page))
    check("含 PDF 降级入口", "btnPdf" in page)
    check("降级链接带 fb=1", "pdf=1&fb=1" in page)
    check("已无 simpleUrl 残留", "simpleUrl" not in page)

    sheet_key = final.split("/sheet/")[-1].split("?")[0]
    kkt = urllib.parse.parse_qs(urllib.parse.urlparse(final).query).get("kkt", [""])[0]

    print()
    print("=== 2. 触发 PDF 降级 (?pdf=1&fb=1) ===")
    pdf_url = "%s/sheet/%s?kkt=%s&pdf=1&fb=1" % (
        BASE, sheet_key, urllib.parse.quote(kkt))
    loc, code = "", 0
    try:
        r2 = get(pdf_url, token, allow_redirect=False, timeout=180)
        loc, code = r2.headers.get("Location", ""), r2.status
    except urllib.error.HTTPError as e:      # 3xx 在禁止跳转时会抛异常
        loc, code = e.headers.get("Location", "") if e.headers else "", e.code
    check("返回重定向", code in (301, 302, 307, 308), "HTTP %s" % code)
    check("指向引擎 onlinePreview", "/kk/onlinePreview" in loc, loc[:70])

    print()
    print("=== 3. 引擎 PDF 预览页 ===")
    t0 = time.time()
    r3 = get(BASE + loc, token, timeout=180)
    html = r3.read().decode("utf-8", "replace")
    cost = time.time() - t0
    check("HTTP 200", r3.status == 200, "%.2fs  %d 字节" % (cost, len(html)))
    check("不是'暂不支持'页", "暂不支持" not in html)
    check("是 PDF 预览页", ("pdf" in html.lower() or "viewer" in html.lower()))
    if "暂不支持" in html:
        print("     页面片段:", html[:200])

    print()
    print("=== 4. 记忆机制: 再次申请预览应直接走 PDF ===")
    d2 = call("/api/v1/preview/url",
              {"fileUrl": file_url, "empNo": "EMP0001"}, token)["data"]
    loc4, code4 = "", 0
    try:
        r4 = get(d2["previewUrl"], token, allow_redirect=False, timeout=180)
        loc4, code4 = r4.headers.get("Location", ""), r4.status
    except urllib.error.HTTPError as e:
        loc4, code4 = e.headers.get("Location", "") if e.headers else "", e.code
    check("直接跳 PDF(不再回表格页)", "/kk/onlinePreview" in loc4, loc4[:70])

    print()
    print("结论:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
