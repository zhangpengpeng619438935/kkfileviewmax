# -*- coding: utf-8 -*-
"""拆解 Univer 表格快照的生成耗时（容器内跑）。"""
import sys
import time

sys.path.insert(0, "/app")
from openpyxl import load_workbook  # noqa: E402

path = sys.argv[1] if len(sys.argv) > 1 else "/app/samples/real.xlsx"
raw = open(path, "rb").read()
print("源文件 %.1f MB" % (len(raw) / 1048576))

import io

t0 = time.time()
wb = load_workbook(io.BytesIO(raw), data_only=False)
t1 = time.time()
print("load_workbook(data_only=False) %.2fs" % (t1 - t0))
t2 = time.time()
try:
    wb2 = load_workbook(io.BytesIO(raw), data_only=True)
    print("load_workbook(data_only=True)  %.2fs" % (time.time() - t2))
except Exception as e:
    print("data_only 失败", e)

ws = wb[wb.sheetnames[0]]
print("sheet=%s 尺寸 %sx%s" % (ws.title, ws.max_row, ws.max_column))

t3 = time.time()
n = 0
for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row or 1, 8000),
                        max_col=min(ws.max_column or 1, 300)):
    for c in row:
        if c.value is not None:
            n += 1
print("遍历单元格 %.2fs（非空 %d）" % (time.time() - t3, n))

from app import univer_data  # noqa: E402

t4 = time.time()
data = univer_data.xlsx_to_workbook(raw, "real.xlsx")
print("xlsx_to_workbook 总计 %.2fs，快照 %d KB"
      % (time.time() - t4, len(str(data)) // 1024))
