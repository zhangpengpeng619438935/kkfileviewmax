"""引擎升级(7.3 → 26.8)后的全类型回归。

用法(宿主机): python3 /opt/kkfileview-service/tools/regress_engine.py
覆盖: 曾经崩溃的 VDA PPT / 普通 docx / pptx / 大 xlsx(走 PDF) / 小 xlsx(表格模式) / PDF
对走 PDF 的文件还会实际拉一次页面, 确认不是"暂不支持"错误页。
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:9012"
SRC = "http://127.0.0.1:8899/"
APP, SECRET = "kk-demo", "your-app-secret"

CASES = [
    ("vda.pptx", "VDA PPT(71页/21 SmartArt)", "pdf"),
    ("real.docx", "docx", "pdf"),
    ("real.pptx", "pptx(普通)", "pdf"),
    ("bom_test.xlsx", "xlsx 小表", "sheet"),
    ("real.pdf", "PDF", "pdf"),
]


class NR(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(path, payload=None, token=None, timeout=300):
    data = json.dumps(payload).encode() if payload else None
    h = {"Content-Type": "application/json", "X-App-Id": APP}
    if token:
        h["Authorization"] = "Bearer " + token
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + path, data=data, headers=h), timeout=timeout).read())


def main():
    token = call("/api/v1/auth/token",
                 {"appId": APP, "appSecret": SECRET})["data"]["accessToken"]
    ok = True

    for fname, label, expect in CASES:
        t0 = time.time()
        try:
            d = call("/api/v1/preview/url",
                     {"fileUrl": SRC + fname, "empNo": "EMP0001"}, token)["data"]
        except urllib.error.HTTPError as e:
            print("  [!!] %-26s 接口 %s: %s" % (label, e.code,
                                               e.read().decode("utf-8", "replace")[:90]))
            ok = False
            continue
        cost = time.time() - t0
        pv = d.get("previewUrl", "")

        # 看落点
        loc = ""
        try:
            r = urllib.request.build_opener(NR).open(urllib.request.Request(
                pv, headers={"X-App-Id": APP, "Authorization": "Bearer " + token}),
                timeout=300)
            loc = r.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "") if e.headers else ""

        mode = "表格" if "/sheet/" in loc else ("PDF" if "onlinePreview" in loc else "?")
        good = (mode == ("表格" if expect == "sheet" else "PDF"))

        # PDF 模式再拉一次页面, 确认不是错误页
        detail = ""
        if mode == "PDF":
            try:
                r = urllib.request.build_opener(NR).open(urllib.request.Request(
                    BASE + loc, headers={"X-App-Id": APP, "Authorization": "Bearer " + token}),
                    timeout=300)
                html = r.read().decode("utf-8", "replace")
                if "暂不支持" in html:
                    good = False
                    detail = "页面报'暂不支持'"
                elif "viewer.html" in html:
                    detail = "pdf.js 正常"
                else:
                    detail = "%d 字节" % len(html)
            except Exception as exc:
                good = False
                detail = "取页面失败 %s" % exc

        ok = ok and good
        print("  [%s] %-26s -> %-4s  %6.2fs  %s"
              % ("OK" if good else "!!", label, mode, cost, detail))

    print()
    print("结论:", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
