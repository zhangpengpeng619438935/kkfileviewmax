"""探测引擎对某类文件实际走的是 pdf.js(矢量) 还是 图片(位图) 渲染。

用法: docker exec -i kk-gateway python3 - < tools/probe_render_mode.py
"""
import base64
import re
import urllib.parse
import urllib.request

ENGINE = "http://kkfileview:8012"
FILES = ["real.pdf", "real.docx", "real.pptx", "real.xlsx"]


def probe(fname):
    raw = "http://127.0.0.1:8899/" + fname
    enc = urllib.parse.quote_plus(base64.b64encode(raw.encode()).decode())
    url = "%s/onlinePreview?url=%s" % (ENGINE, enc)
    try:
        html = urllib.request.urlopen(url, timeout=120).read().decode("utf-8", "replace")
    except Exception as exc:
        print("%-12s 请求失败 %s" % (fname, exc))
        return
    low = html.lower()
    mode = []
    if "pdf.js" in low or "pdfjs" in low or "viewer.js" in low:
        mode.append("pdf.js")
    if re.search(r"<img[^>]+src=", html, re.I):
        mode.append("<img>")
    if "getcorsfile" in low:
        mode.append("getCorsFile")
    if "暂不支持" in html:
        mode.append("暂不支持")
    m = re.search(r"currentDocumentUrl\s*=\s*'([^']+)'", html)
    print("%-12s %8d 字节  模式=%-24s 文档URL=%s"
          % (fname, len(html), ",".join(mode) or "未知",
             (m.group(1)[:60] if m else "-")))
    # 找出页面里指向真实文档的地址(判断是 .pdf 还是 .jpg)
    for pat in [r"'([^']*\.pdf[^']*)'", r"'([^']*\.jpg[^']*)'", r"'([^']*\.png[^']*)'"]:
        hits = re.findall(pat, html)
        if hits:
            print("              %s -> %s" % (pat.split("\\")[0][:8], hits[0][:70]))
            break


for f in FILES:
    probe(f)
