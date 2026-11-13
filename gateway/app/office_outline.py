"""PPTX 快速内容页(不经过 LibreOffice)。

为什么需要:
    119 页 / 42MB 的课件, LibreOffice 转换要 21 秒(7.3)甚至 56 秒(26.8)。
    用户要求"打开不超过 2 秒", 冷转换做不到, 所以先给一个秒开的内容页:
    按页列出文字 + 配图, 后台继续转 PDF, 转好后页面自动刷新成高清 PDF。

只解析 OOXML, 不依赖 Office 组件, 实测 119 页约 0.2 秒。
原始 pptx 会缓存成 <key>.src.pptx, 图片按需从 zip 里读取, 不再单独解压一份。
"""
import html
import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET

TAG_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

# 浏览器能直接显示的位图/矢量图。emf/wmf/wdp 这类 Windows 元文件浏览器不认,
# 放进去只会显示成"碎图"图标, 所以内容页直接跳过(老版 .ppt 升级后大量是 emf)。
IMG_OK = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff")


def _rels(z, part):
    base = os.path.dirname(part)
    rels_path = "%s/_rels/%s.rels" % (base, os.path.basename(part))
    try:
        root = ET.fromstring(z.read(rels_path))
    except KeyError:
        return {}
    return {r.get("Id"): (r.get("Target") or "") for r in root}


def _slide_list(z):
    """按 presentation.xml 的顺序返回幻灯片路径(不能按文件名排序: slide10 < slide2)。"""
    try:
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
    except KeyError:
        return []
    rels = _rels(z, "ppt/presentation.xml")
    lst = pres.find("{%s}sldIdLst" % NS_P)
    if lst is None:
        return []
    order = []
    for sld in lst:
        target = rels.get(sld.get("{%s}id" % NS_R), "")
        if not target:
            continue
        path = target.lstrip("/")
        if not path.startswith("ppt/"):
            path = "ppt/" + path
        order.append(path)
    return order


def build_pptx(data, file_name, key):
    """返回 HTML 字符串。图片通过 /progmedia/<key>/<name> 按需读取。"""
    z = zipfile.ZipFile(io.BytesIO(data))
    slides = _slide_list(z)
    if not slides:
        raise RuntimeError("没有找到幻灯片")

    cards = []
    for idx, spath in enumerate(slides, 1):
        try:
            root = ET.fromstring(z.read(spath))
        except (KeyError, ET.ParseError):
            continue
        paras = []
        for p in root.iter(TAG_A + "p"):
            text = "".join(t.text or "" for t in p.iter(TAG_A + "t")).strip()
            if text:
                paras.append(text)

        imgs = []
        for target in _rels(z, spath).values():
            if "media/" not in target:
                continue
            name = os.path.basename(target)
            if (name and name not in imgs
                    and os.path.splitext(name)[1].lower() in IMG_OK):
                imgs.append(name)
        imgs = imgs[:6]

        body = []
        if paras:
            body.append('<p class="t">%s</p>' % html.escape(paras[0]))
            body.extend("<p>%s</p>" % html.escape(x) for x in paras[1:])
        else:
            body.append('<p class="empty">（本页无文字）</p>')
        if imgs:
            body.append('<div class="imgs">%s</div>' % "".join(
                '<img src="/progmedia/%s/%s?kkt=__KKT__" loading="lazy" alt="">'
                % (key, html.escape(n)) for n in imgs))
        cards.append('<section class="slide"><div class="hd">第 %d / %d 页</div>'
                     '<div class="bd">%s</div></section>'
                     % (idx, len(slides), "".join(body)))

    title = html.escape(file_name or "演示文稿")
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#222;
 font-family:'Microsoft YaHei','PingFang SC',Arial,sans-serif;font-size:14px;line-height:1.7}
#tip{position:sticky;top:0;z-index:30;background:#fff8e6;border-bottom:1px solid #f0e0b8;
 color:#8a6d3b;padding:8px 14px;font-size:12.5px}
#tip b{color:#6b5426}
.wrap{max-width:940px;margin:0 auto;padding:14px 16px 70px}
.slide{border:1px solid #e6e8eb;border-radius:8px;margin:0 0 14px;overflow:hidden;background:#fff}
.hd{background:#f6f7f9;border-bottom:1px solid #eef0f3;color:#5b636e;font-size:12px;padding:6px 12px}
.bd{padding:12px 14px}
.bd p{margin:0 0 6px;white-space:pre-wrap;word-break:break-word}
.bd p.t{font-weight:600;font-size:15px}
.bd p.empty{color:#b0b6bd;font-size:12px}
.imgs{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
.imgs img{max-width:100%%;max-height:320px;border:1px solid #eef0f3;border-radius:4px}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9999;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div id="tip">正在生成高清预览（约 %(eta)d 秒），当前为<b>快速内容版</b>，完成后自动切换。</div>
<div class="wrap">%(cards)s</div>
<div id="wm"></div>
<script>
(function(){
  var n=0;
  var timer=setInterval(function(){
    if(++n>150){clearInterval(timer);return;}
    fetch('/api/v1/preview/ready?f=%(key)s&kkt=%(kkt)s',{cache:'no-store'})
      .then(function(r){return r.json()})
      .then(function(d){ if(d&&d.ready){ location.reload(); } })
      .catch(function(){});
  },2000);
})();
</script>
</body></html>""" % {"title": title, "cards": "".join(cards), "key": key,
                     "kkt": "__KKT__", "wm": "__WM__", "eta": 20}
