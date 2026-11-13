# -*- coding: utf-8 -*-
"""测大 xlsx 快照生成的耗时构成，验证优化手段（容器内跑）。

用法: python3 /tmp/bx2.py <xlsx 路径>
"""
import io
import sys
import time
import zipfile
from xml.etree import ElementTree as ET

from openpyxl import load_workbook

path = sys.argv[1]
raw = open(path, "rb").read()
print("源文件 %.2f MB" % (len(raw) / 1048576))

# 1) 目录结构：多少 sheet、多少 sharedStrings、多少 styles
z = zipfile.ZipFile(io.BytesIO(raw))
names = z.namelist()
print("zip 条目 %d，sharedStrings %s KB，styles %s KB，sheet xml %s"
      % (len(names),
         len(z.read("xl/sharedStrings.xml")) // 1024 if "xl/sharedStrings.xml" in names else "-",
         len(z.read("xl/styles.xml")) // 1024 if "xl/styles.xml" in names else "-",
         [n for n in names if n.startswith("xl/worksheets/sheet")][:3]))

t0 = time.time()
wb = load_workbook(io.BytesIO(raw), data_only=False)
print("load_workbook 普通模式 %.2fs，sheets=%s" % (time.time() - t0, len(wb.sheetnames)))
for name in wb.sheetnames[:5]:
    ws = wb[name]
    print("  %-24s max_row=%-9s max_col=%-5s dim=%s"
          % (name[:24], ws.max_row, ws.max_column, ws.calculate_dimension()))

# 2) 空单元格占比（第一个 sheet 前 2000 行）
ws = wb[wb.sheetnames[0]]
t1 = time.time()
tot = with_val = with_style = 0
for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 2000),
                        max_col=min(ws.max_column or 1, 300)):
    for c in row:
        tot += 1
        if c.value is not None:
            with_val += 1
        if c.has_style:
            with_style += 1
print("前 2000 行：%d 格，有值 %d，有样式 %d，扫描 %.2fs"
      % (tot, with_val, with_style, time.time() - t1))

# 3) 当前实现
from app import univer_data  # noqa: E402

t2 = time.time()
snap = univer_data.xlsx_to_workbook(raw, "x.xlsx")
print("xlsx_to_workbook 现状 %.2fs，快照 %.0f KB"
      % (time.time() - t2, len(str(snap)) / 1024))

# 4) read_only 遍历同样范围
t3 = time.time()
wbr = load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
wsr = wbr[wbr.sheetnames[0]]
n = 0
for row in wsr.iter_rows(min_row=1, max_row=2000, max_col=300):
    for c in row:
        if c.value is not None:
            n += 1
print("read_only 扫 2000x300 %.2fs（非空 %d）" % (time.time() - t3, n))
