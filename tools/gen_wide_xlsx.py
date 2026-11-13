"""生成一个"声明列数远大于实际列数"的测试文件, 用于验证 grid 尺寸修复。

用法(在网关容器内): docker exec -i kk-gateway python3 - < tools/gen_wide_xlsx.py
"""
from openpyxl import Workbook
from openpyxl.styles import Font

wb = Workbook()
ws = wb.active
ws.title = "清单"
for r in range(1, 200):                 # 200 行 × 13 列真实数据
    for c in range(1, 14):
        ws.cell(row=r, column=c, value="数据%d-%d" % (r, c))
ws.cell(row=1, column=80).font = Font(bold=True)   # 制造 80 列的假声明
wb.save("/tmp/wide_format.xlsx")
print("已生成: openpyxl 报 max_row=%s max_column=%s (实际数据只到 13 列)"
      % (ws.max_row, ws.max_column))
