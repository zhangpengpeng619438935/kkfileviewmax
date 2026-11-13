# -*- coding: utf-8 -*-
"""判断 CAD 转出的 PDF 是不是彩色：统计渲染后像素里的饱和色比例。

用法: python pdf_color.py <pdf> [更多 pdf ...]
"""
import io
import sys

import pypdfium2 as pdfium
from PIL import Image


def probe(path):
    try:
        doc = pdfium.PdfDocument(path)
        pg = doc[0]
        w, h = pg.get_size()
        scale = 1200.0 / max(1.0, w)
        im = pg.render(scale=scale).to_pil().convert("RGB")
        doc.close()
    except Exception as exc:
        print("%-60s 打不开: %s" % (path.split("/")[-1][:58], exc))
        return
    px = list(im.getdata())
    total = len(px)
    cols = {}
    sat = 0
    for (r, g, b) in px:
        if max(r, g, b) - min(r, g, b) > 40 and max(r, g, b) > 60:
            sat += 1
            k = (r // 32 * 32, g // 32 * 32, b // 32 * 32)
            cols[k] = cols.get(k, 0) + 1
    top = sorted(cols.items(), key=lambda x: -x[1])[:4]
    print("%-58s 饱和色 %.3f%%  主色 %s  -> %s" % (
        path.split("/")[-1][:56], sat * 100.0 / total,
        ", ".join("#%02x%02x%02x(%d)" % (c[0], c[1], c[2], n) for c, n in top) or "无",
        "彩色" if sat * 100.0 / total > 0.05 else "单色"))


for p in sys.argv[1:]:
    probe(p)
