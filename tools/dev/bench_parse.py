#!/usr/bin/env python3
"""对比两种 xlsx 解析速度: openpyxl(全量) vs 直接读 OOXML。"""
import glob
import time
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def raw_parse(path):
    """直接解析 OOXML: 取 sharedStrings + 每个 sheet 的单元格。"""
    t0 = time.time()
    z = zipfile.ZipFile(path)
    # sharedStrings
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    t_shared = time.time() - t0

    # workbook 里的 sheet 顺序与 rId
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rel_map = {r.get("Id"): r.get("Target") for r in rels}
    sheets = []
    for sh in wb.find("m:sheets", NS):
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rel_map.get(rid, "")
        sheets.append((sh.get("name"), "xl/" + target.lstrip("/").replace("xl/", "")))

    total = 0
    per_sheet = []
    for name, target in sheets:
        try:
            data = z.read(target)
        except KeyError:
            continue
        root = ET.fromstring(data)
        rows = 0
        cells = 0
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            rows += 1
            cells += len(row.findall("m:c", NS))
        total += cells
        per_sheet.append((name, rows, cells))
    return time.time() - t0, t_shared, total, per_sheet


def openpyxl_parse(path):
    t0 = time.time()
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    total = 0
    per = []
    for ws in wb.worksheets:
        r = c = 0
        for row in ws.iter_rows(values_only=True):
            r += 1
            c += sum(1 for v in row if v is not None)
        total += c
        per.append((ws.title, r, c))
    wb.close()
    return time.time() - t0, total, per


for p in sorted(glob.glob("/tmp/verify/*.xlsx")):
    print("=" * 66)
    print(p.split("/")[-1])
    rt, ts, total, per = raw_parse(p)
    print("  OOXML 直读: %.3fs (sharedStrings %.3fs) 单元格 %d" % (rt, ts, total))
    for n, r, c in per:
        print("     %-24s 行=%-6d 格=%d" % (n[:24], r, c))
    ot, ototal, oper = openpyxl_parse(p)
    print("  openpyxl(只读): %.3fs 单元格 %d" % (ot, ototal))
