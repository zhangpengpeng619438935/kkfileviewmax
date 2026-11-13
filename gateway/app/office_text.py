"""Office 文件"转 PDF 失败"时的文字提取降级。

背景: LibreOffice 遇到页数多、SmartArt 多的 pptx 会直接崩溃(SIGABRT, exit 134),
连 `--convert-to odp` 都失败。这类文件在服务端根本转不出 PDF。
此时退一步: 直接解析 OOXML 里的文字, 渲染成可按页浏览的阅读页——
排版和图形会丢, 但**内容能看**, 总比打不开强。

不依赖 python-pptx, 只用标准库(zipfile + 正则), 71 页的 PPT 解析耗时 < 1 秒。
"""
import html
import io
import re
import zipfile

MAX_SLIDES = 300          # 最多展示的页数
MAX_BLOCKS_PER_SLIDE = 200  # 单页最多文字块


def _watermark_url() -> str:
    from . import sheet
    return sheet.watermark_bg_url()


def _page(title: str, subtitle: str, body: str) -> str:
    wm = _watermark_url()
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#222;
 font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;font-size:14px;line-height:1.75}
header{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid #e6e8eb;
 padding:12px 20px}
header h1{margin:0;font-size:15px;font-weight:600}
header p{margin:6px 0 0;font-size:12px;color:#8a9099}
.wrap{max-width:900px;margin:0 auto;padding:18px 20px 60px}
.slide{border:1px solid #e6e8eb;border-radius:8px;margin:0 0 14px;overflow:hidden}
.slide-h{background:#f6f7f9;padding:7px 12px;font-size:12px;color:#5b636e;
 border-bottom:1px solid #eef0f3;display:flex;justify-content:space-between}
.slide-b{padding:12px 14px}
.slide-b p{margin:0 0 6px;white-space:pre-wrap;word-break:break-word}
.slide-b p.t{font-weight:600;font-size:15px}
.empty{color:#b0b6bd;font-size:12px}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9999;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<header><h1>%(title)s</h1><p>%(sub)s</p></header>
<div class="wrap">%(body)s</div><div id="wm"></div>
</body></html>""" % {"title": html.escape(title), "sub": html.escape(subtitle),
                     "body": body, "wm": wm}


def _slide_block(idx: int, total: int, texts: list) -> str:
    if not texts:
        inner = '<span class="empty">（本页无文字，可能是纯图片/图形页）</span>'
    else:
        rows = []
        for i, t in enumerate(texts[:MAX_BLOCKS_PER_SLIDE]):
            cls = ' class="t"' if i == 0 and len(t) <= 40 else ""
            rows.append("<p%s>%s</p>" % (cls, html.escape(t)))
        inner = "".join(rows)
    return ('<section class="slide"><div class="slide-h"><span>第 %d 页</span>'
            '<span>%d / %d</span></div><div class="slide-b">%s</div></section>'
            % (idx, idx, total, inner))


def pptx_to_html(data: bytes, name: str) -> str:
    """把 pptx 的每页文字提取出来, 渲染成阅读页。"""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    slides = [n for n in names
              if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    slides.sort(key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
    if not slides:
        raise RuntimeError("未找到幻灯片内容")

    total = min(len(slides), MAX_SLIDES)
    blocks = []
    for i, n in enumerate(slides[:MAX_SLIDES], 1):
        try:
            xml = z.read(n).decode("utf-8", "replace")
        except Exception:
            continue
        raw = re.findall(r"<a:t>(.*?)</a:t>", xml, re.S)
        texts = [html.unescape(t).strip() for t in raw]
        texts = [t for t in texts if t]
        blocks.append(_slide_block(i, total, texts))

    sub = ("共 %d 页。该文件含大量图形/SmartArt，转换引擎无法生成 PDF，"
           "已降级为文字内容（不含排版与图片）。" % total)
    return _page(name or "演示文稿（文字模式）", sub, "".join(blocks))


def docx_to_html(data: bytes, name: str) -> str:
    """docx 转 PDF 失败时, 提取正文段落文字。"""
    z = zipfile.ZipFile(io.BytesIO(data))
    try:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    except KeyError:
        raise RuntimeError("未找到文档正文")
    paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
    rows = []
    for p in paras:
        texts = [html.unescape(t).strip() for t in re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)]
        line = "".join(t for t in texts if t)
        if line:
            rows.append("<p>%s</p>" % html.escape(line))
    if not rows:
        raise RuntimeError("未提取到文字内容")
    sub = "该文件转换失败，已降级为文字内容（不含排版与图片）。"
    return _page(name or "文档（文字模式）", sub,
                 '<section class="slide"><div class="slide-b">%s</div></section>'
                 % "".join(rows[:3000]))


def extract(data: bytes, name: str, ext: str) -> str:
    if ext in ("pptx", "ppt"):
        return pptx_to_html(data, name)
    if ext in ("docx", "doc"):
        return docx_to_html(data, name)
    raise RuntimeError("该类型不支持文字降级")
