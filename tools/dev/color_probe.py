# -*- coding: utf-8 -*-
"""统计一张图里到底有多少种颜色，用来回答"能不能是彩色的"。

用法: python color_probe.py <图片路径>
"""
import sys
from collections import Counter

from PIL import Image

im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
c = Counter(im.getdata())
total = w * h
print("尺寸 %dx%d  唯一颜色 %d" % (w, h, len(c)))
for col, n in c.most_common(12):
    print("  %-16s %8d  %5.2f%%" % (str(col), n, n * 100.0 / total))
# 彩色判定：出现明显的饱和色（R/G/B 相差 > 40）且占比 > 0.05%
sat = sum(n for (r, g, b), n in c.items()
          if max(r, g, b) - min(r, g, b) > 40 and max(r, g, b) > 60)
print("饱和色像素 %d (%.3f%%) -> %s" % (sat, sat * 100.0 / total,
                                   "彩色" if sat * 100.0 / total > 0.05 else "黑白/灰"))
