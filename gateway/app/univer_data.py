# -*- coding: utf-8 -*-
"""把 xlsx / xls / ppt / pptx 转成 Univer 1.1.0 的快照，供 /univer 预览页渲染。

为什么不用 Univer 官方导入：
    Univer 1.1.0 的文件级导入（XLSX/PPTX → snapshot）在
    `@univerjs-pro/sheets-exchange-client` 与 Server SDK 里，属于商业授权；
    免费 Web SDK（Apache-2.0）只吃 snapshot。所以这里自己把文件解析成 snapshot：
      - xlsx/xls → IWorkbookData（单元格、样式、合并、列宽行高、冻结、公式）
      - ppt/pptx → ISlideData（每页一张由 PDFium 从 LibreOffice 出的 PDF 渲染的位图）

幻灯片为什么用位图而不是解 OOXML 形状：
    免费版 Univer 没有形状渲染（在 @univerjs-pro/slides），自己解 pptx 的
    形状树既做不到版式还原（SmartArt/艺术字/母版继承），也没必要——
    页面已经是 LibreOffice 排好版的样子，翻页、缩放、页面列表仍由 Univer 提供。
"""
import io
import datetime
import os
import re
import threading
import zipfile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries

from .xlsx_html import _col_px, _row_px

# Univer 的枚举（与 @univerjs/core 保持一致，避免前端做二次映射）
H_LEFT, H_CENTER, H_RIGHT = 1, 2, 3
V_TOP, V_MIDDLE, V_BOTTOM = 1, 2, 3
BORDER_THIN = 1
CELL_STRING, CELL_NUMBER, CELL_BOOLEAN = 1, 2, 3
UNDERLINE_SINGLE = 1

MAX_ROWS = 8000
MAX_COLS = 300


# ------------------------------------------------------------------ 颜色
def _rgb(color):
    """openpyxl 的 Color → Univer 的 {rgb: 'rgb(r,g,b)'}。"""
    if color is None:
        return None
    try:
        if color.type == "theme":
            # 主题色不做精确还原（需要读 theme1.xml），给一个中性值保证可读
            return None
        if color.type == "indexed":
            return None
        val = color.rgb
    except AttributeError:
        return None
    if not isinstance(val, str) or len(val) not in (6, 8):
        return None
    if len(val) == 8:
        if val[:2] == "00":          # 完全透明
            return None
        val = val[2:]
    r, g, b = int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
    return {"rgb": "rgb(%d,%d,%d)" % (r, g, b)}


def _side(style, color):
    if not style:
        return None
    s = {"s": BORDER_THIN}
    c = _rgb(color)
    if c:
        s["cl"] = c
    return s


# ------------------------------------------------------------------ 样式
def _style_of(cell):
    """把一个单元格的样式压成 Univer 的 IStyleData；没有样式返回 None。"""
    st = {}
    f = cell.font
    if f is not None:
        if f.name:
            st["ff"] = f.name
        if f.size:
            st["fs"] = int(round(float(f.size)))
        if f.bold:
            st["bl"] = 1
        if f.italic:
            st["it"] = 1
        if f.strike:
            st["st"] = {"s": 1}
        if f.underline:
            st["ul"] = {"s": UNDERLINE_SINGLE}
        c = _rgb(f.color)
        if c:
            st["cl"] = c

    fill = cell.fill
    if fill is not None and getattr(fill, "patternType", None) not in (None, "none"):
        c = _rgb(getattr(fill, "fgColor", None))
        if c:
            st["bg"] = c

    b = cell.border
    if b is not None:
        bd = {}
        for side, val in (("l", b.left), ("r", b.right), ("t", b.top), ("b", b.bottom)):
            if val is not None and val.style:
                s = _side(val.style, val.color)
                if s:
                    bd[side] = s
        if bd:
            st["bd"] = bd

    al = cell.alignment
    if al is not None:
        h = (al.horizontal or "").lower()
        if h in ("left", "center", "right"):
            st["ht"] = {"left": H_LEFT, "center": H_CENTER, "right": H_RIGHT}[h]
        elif h == "justify":
            st["ht"] = H_LEFT
        v = (al.vertical or "").lower()
        if v in ("top", "center", "bottom"):
            st["vt"] = {"top": V_TOP, "center": V_MIDDLE, "bottom": V_BOTTOM}[v]
        if al.wrap_text:
            st["tb"] = 1

    nf = getattr(cell, "number_format", None)
    if nf and nf != "General":
        st["n"] = {"pattern": nf}

    return st or None


# ------------------------------------------------------------------ 单元格
def _cell_value(cell, cached):
    """返回 (value, cellType) 或 None。"""
    v = cell.value
    if v is None:
        return None
    if cell.data_type == "f" or (isinstance(v, str) and v.startswith("=")):
        formula = v[1:] if isinstance(v, str) and v.startswith("=") else str(v)
        out = {"f": formula}
        cv = cached.get((cell.row, cell.column))
        if isinstance(cv, (int, float)) and not isinstance(cv, bool):
            out["v"] = cv
            out["t"] = CELL_NUMBER
        elif isinstance(cv, str) and cv:
            out["v"] = cv
            out["t"] = CELL_STRING
        return out
    if isinstance(v, bool):
        return {"v": 1 if v else 0, "t": CELL_BOOLEAN}
    if isinstance(v, (int, float)):
        return {"v": v, "t": CELL_NUMBER}
    if isinstance(v, datetime.datetime):
        # datetime 才有 sep 参数；截到秒，微秒对表格显示没有意义
        return {"v": v.isoformat(sep=" ")[:19], "t": CELL_STRING}
    if isinstance(v, (datetime.date, datetime.time)):
        # date / time 的 isoformat() **不接受 sep**，原来用 hasattr(v, "isoformat")
        # 一律当 datetime 处理，遇到纯日期列或纯时间列就抛
        # TypeError: 'sep' is an invalid keyword argument for isoformat()
        # —— 不是这一格坏了，是整个快照生成直接失败（实测一张产能分析表里
        # 180 个"班次时间"单元格，导致表格打不开、只能干等页面超时）。
        return {"v": v.isoformat(), "t": CELL_STRING}
    text = str(v)
    if not text:
        return None
    return {"v": text, "t": CELL_STRING}


def xlsx_to_workbook(data: bytes, file_name: str = "") -> dict:
    """xlsx（或已转好的 xls）→ Univer IWorkbookData。"""
    wb_f = load_workbook(io.BytesIO(data), data_only=False)
    try:
        wb_v = load_workbook(io.BytesIO(data), data_only=True)
    except Exception:
        wb_v = None

    base_pt = 11
    styles, style_ids = {}, {}
    sheet_order, sheets = [], {}
    total_bad = 0          # 全簿"降级为文本"的单元格数，只用于日志

    for name in wb_f.sheetnames:
        ws = wb_f[name]
        order_i = len(sheet_order)
        sheet_id = "s%d" % (order_i + 1)
        sheet_order.append(sheet_id)

        max_r = min(ws.max_row or 1, MAX_ROWS)
        max_c = min(ws.max_column or 1, MAX_COLS)

        # 缓存值（data_only 那份）
        cached = {}
        if wb_v is not None and name in wb_v.sheetnames:
            wsv = wb_v[name]
            for row in wsv.iter_rows(min_row=1, max_row=max_r, max_col=max_c):
                for c in row:
                    if c.value is not None:
                        cached[(c.row, c.column)] = c.value

        cell_data = {}
        bad_cells = 0
        for row in ws.iter_rows(min_row=1, max_row=max_r, max_col=max_c):
            for cell in row:
                # 单格出错不能拖垮整本：一格解析不了就退成文本，其它照常。
                # 以前这里没有任何保护，一个意外类型（如 date/time 的 isoformat(sep)）
                # 就让整份快照生成失败 → 用户看到的是表格打不开、页面干等。
                try:
                    item = _cell_value(cell, cached)
                    st = _style_of(cell)
                except Exception:
                    bad_cells += 1
                    try:
                        raw = cell.value
                        item = ({"v": str(raw)[:1000], "t": CELL_STRING}
                                if raw is not None else None)
                    except Exception:
                        item = None
                    st = None
                if item is None and st is None:
                    continue
                if item is None:
                    item = {}
                if st is not None:
                    key = repr(sorted(st.items()))
                    sid = style_ids.get(key)
                    if sid is None:
                        sid = "st%d" % (len(styles) + 1)
                        style_ids[key] = sid
                        styles[sid] = st
                    item["s"] = sid
                cell_data.setdefault(cell.row - 1, {})[cell.column - 1] = item

        merges = []
        for rng in ws.merged_cells.ranges:
            c1, r1, c2, r2 = range_boundaries(str(rng))
            merges.append({"startRow": r1 - 1, "startColumn": c1 - 1,
                           "endRow": r2 - 1, "endColumn": c2 - 1})

        col_data = {}
        # read_only 的 ReadOnlyWorksheet 没有这两个属性，直接访问会抛
        # AttributeError（不是返回 None）。用 getattr 兜住：没有列宽就用默认，
        # 不能因为"取不到样式"让整份快照失败。
        for letter, dim in (getattr(ws, "column_dimensions", None) or {}).items():
            if not dim.width:
                continue
            idx = 0
            for ch in letter:
                idx = idx * 26 + (ord(ch) - 64)
            col_data[idx - 1] = {"w": _col_px(dim.width, base_pt)}
            if dim.hidden:
                col_data[idx - 1]["hd"] = 1

        row_data = {}
        for i, dim in (getattr(ws, "row_dimensions", None) or {}).items():
            if dim.height:
                row_data[i - 1] = {"h": _row_px(dim.height, 15, base_pt)}
            elif dim.hidden:
                row_data[i - 1] = {"hd": 1}

        freeze = None
        fp = ws.freeze_panes
        if fp:
            try:
                col = re.match(r"([A-Z]+)", str(fp)).group(1)
                row = int(re.search(r"(\d+)", str(fp)).group(1))
                x, y = 0, 0
                for ch in col:
                    x = x * 26 + (ord(ch) - 64)
                x, y = x - 1, row - 1
                if x or y:
                    freeze = {"xSplit": x, "ySplit": y,
                              "startRow": y, "startColumn": x}
            except Exception:
                freeze = None

        total_bad += bad_cells
        sheets[sheet_id] = {
            "id": sheet_id,
            "name": name,
            "tabColor": "",
            "hidden": 1 if ws.sheet_state != "visible" else 0,
            "rowCount": max(max_r + 40, 100),
            "columnCount": max(max_c + 8, 26),
            "defaultRowHeight": _row_px(15, 15, base_pt),
            "defaultColumnWidth": _col_px(8.43, base_pt),
            "cellData": cell_data,
            "mergeData": merges,
            "rowData": row_data,
            "columnData": col_data,
            "freeze": freeze or {"xSplit": 0, "ySplit": 0, "startRow": -1, "startColumn": -1},
            "zoomRatio": 1,
            "scrollLeft": 0,
            "scrollTop": 0,
        }

    if total_bad:
        # 只记一条：出现说明又有"解析不了的值类型"，但这次没让整本表格挂掉
        print("WARN 表格快照有 %d 个单元格降级为文本: %s" % (total_bad, file_name),
              flush=True)

    return {
        "id": "wb_" + re.sub(r"\W", "", file_name)[:16] if file_name else "",
        "name": file_name or "工作簿",
        "appVersion": "1.1.0",
        "locale": "zhCN",
        "styles": styles,
        "sheetOrder": sheet_order,
        "sheets": sheets,
    }


def looks_like_xlsx(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


# ------------------------------------------------------------------ 大表格预检
_DIM = re.compile(rb'<dimension[^>]*ref="([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?"')


def _col_no(letters: bytes) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ch - 64)
    return n


def heavy_reason(data: bytes, cols_limit: int = 0, cells_limit: int = 0) -> str:
    """判断这个 xlsx 是否"大到不该用 openpyxl 建对象"，返回原因（空串=正常）。

    只读 zip 里每张表的 <dimension>（在 XML 最前面，几十字节），
    不做任何解析，耗时几毫秒。命中后应改走 xlsx_html 的轻量视图：
    顺序访问同一份《ZL-F32 质量门评审表》实测 28s → 2.1s。
    """
    from . import config
    cols_limit = cols_limit or config.SHEET_HEAVY_COLS
    cells_limit = cells_limit or config.SHEET_HEAVY_CELLS
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return ""
    total = 0
    widest = 0
    for name in z.namelist():
        if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
            continue
        try:
            head = z.read(name)[:4096]
        except Exception:
            continue
        m = _DIM.search(head)
        if not m:
            continue
        c1 = _col_no(m.group(1))
        r1 = int(m.group(2))
        c2 = _col_no(m.group(3)) if m.group(3) else c1
        r2 = int(m.group(4)) if m.group(4) else r1
        wide, high = max(c1, c2), max(r1, r2)
        widest = max(widest, wide)
        total += wide * high
    if widest > cols_limit:
        return "单表 %d 列（阈值 %d），openpyxl 会按整行建对象" % (widest, cols_limit)
    if total > cells_limit:
        return "全簿 dimension 合计 %d 格（阈值 %d）" % (total, cells_limit)
    return ""


# ------------------------------------------------------------------ 幻灯片
def slides_snapshot(pages, file_name: str = "") -> dict:
    """把"每页一张图"的列表包装成 Univer 的 ISlideData。

    pages: [{"width": 1200, "height": 675, "src": "/api/v1/univer/page/..."}]
    width/height 是**像素**尺寸，由调用方按容器大小算好——Univer 的幻灯片画布
    1:1 画像素，页面给多大就占多大，所以尺寸必须和容器对齐，否则会被裁。
    """
    if not pages:
        raise ValueError("PPT 没有可显示的页")
    w = int(pages[0]["width"])
    h = int(pages[0]["height"])
    body_pages, order = {}, []
    for i, p in enumerate(pages):
        pid = "sp%d" % (i + 1)
        pw = int(p.get("width") or w)
        ph = int(p.get("height") or h)
        body_pages[pid] = {
            "id": pid,
            "pageType": 0,
            "zIndex": i,
            "title": "第 %d 页" % (i + 1),
            "description": "",
            "pageBackgroundFill": {"rgb": "rgb(255,255,255)"},
            "pageElements": {
                "se%d" % (i + 1): {
                    "id": "se%d" % (i + 1),
                    "zIndex": 0,
                    "left": 0,
                    "top": 0,
                    "width": pw,
                    "height": ph,
                    "title": "",
                    "description": "",
                    "type": 1,                      # PageElementType.IMAGE
                    "image": {"imageProperties": {"contentUrl": p["src"]}},
                }
            },
        }
        order.append(pid)
    return {
        "id": "sl_" + re.sub(r"\W", "", file_name)[:16] if file_name else "",
        "title": file_name or "演示文稿",
        "pageSize": {"width": w, "height": h},
        "body": {"pages": body_pages, "pageOrder": order},
    }


# ------------------------------------------------------------------ PDF 页
def pdf_page_info(pdf_path: str):
    """返回 [(宽pt, 高pt), ...]，一页一项。"""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    try:
        return [tuple(doc[i].get_size()) for i in range(len(doc))]
    finally:
        try:
            doc.close()
        except Exception:
            pass


# PDFium 不是线程安全的：几十页同时打开同一份 PDF 会直接崩（实测 119 页 PPT
# 一次性拉缩略图，后排页面全 500）。用一把进程内锁串行化，单页渲染只要几十毫秒，
# 串行对体验没有影响。
_PDF_LOCK = threading.Lock()


def _render_jpg_locked(pdf_path: str, width: int, page: int, quality: int) -> bytes:
    import io as _io
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    try:
        idx = max(0, min(page, len(doc) - 1))
        pg = doc[idx]
        w_pt, h_pt = pg.get_size()
        bitmap = pg.render(scale=float(width) / max(1.0, w_pt))
        pil = bitmap.to_pil().convert("RGB")
        buf = _io.BytesIO()
        # 幻灯片是照片/图表混排，JPEG 比 PNG 小一个数量级
        pil.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    finally:
        try:
            doc.close()
        except Exception:
            pass


def _render_jpg_sync(pdf_path: str, width: int, page: int, quality: int = 82) -> bytes:
    with _PDF_LOCK:
        return _render_jpg_locked(pdf_path, width, page, quality)


async def render_page_jpg(pdf_path: str, page: int, width: int, cache_path: str,
                          quality: int = 82) -> bytes:
    """把 PDF 的某一页渲染成 JPEG（带磁盘缓存）。"""
    import asyncio
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                return f.read()
        except OSError:
            pass
    data = await asyncio.get_event_loop().run_in_executor(
        None, _render_jpg_sync, pdf_path, width, page, quality)
    tmp = cache_path + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, cache_path)
    except OSError:
        pass
    return data
