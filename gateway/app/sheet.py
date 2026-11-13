"""Excel / CSV 表格渲染: 不经过 LibreOffice 转 PDF, 直接按行列渲染成网页表格。

为什么需要它:
  kkFileView 对 xlsx 的处理是"LibreOffice 转 PDF 再 pdf.js 预览", 转 PDF 时会按 A4
  纵向分页——宽表会被切成几十页、列被截断, 与本地 Excel 打开的效果差异很大
  (实测本项目的 APQP 周会表被切成 60 页)。表格模式直接读单元格渲染, 行列、列宽、
  合并单元格、数字格式都保留, 清晰且可滚动, 更接近 Excel 原貌。
"""
import asyncio
import csv
import datetime
import html
import io
import json
import os
import re
import urllib.parse

from openpyxl.utils import get_column_letter

from . import config, engine

SHEET_DIR = "/app/sheets"
MAX_ROWS = 300          # 每个工作表最多渲染行数(超出提示截断)
MAX_COLS = 80           # 每个工作表最多渲染列数
MAX_FILE_MB = 25        # 超过该大小走 PDF 模式(避免内存压力)
# 超过该大小改用流式解析(read_only): 全量解析大文件是分钟级的, 流式只读需要的单元格, 秒级完成
STREAM_MB = 4
# 渲染超时(秒): 超时则回退 PDF 模式, 不让任务无限挂起
# 注意: asyncio.wait_for 无法真正杀掉线程池里的解析, 超时只是"我们不等了",
# 所以这个值要按"用户能接受的最长等待"来定, 见 config.SHEET_MAX_BUILD_SEC
BUILD_TIMEOUT = 60


class SheetTooHeavy(Exception):
    """该文件不适合表格模式(过重/过慢), 调用方应直接回退 PDF 预览。

    表格组件在浏览器里同步初始化, 卡住时前端的超时与降级都执行不到,
    因此这里在服务端提前放弃, 而不是把风险留给用户。
    """
# Luckysheet 是重组件, 数据量过大时初始化会明显变慢甚至卡住主线程,
# 这里对"整份文件"设总量上限, 超限后停止渲染后续工作表(前几张仍可正常查看)。
MAX_SHEETS = 12           # 最多渲染的工作表数
MAX_TOTAL_CELLS = 20000   # 全部工作表累计单元格上限
MAX_MERGE_PER_SHEET = 300  # 单个工作表的合并单元格上限
MIN_ROWS, MIN_COLS = 60, 20  # row/column 保底值(Luckysheet 必需字段, 空表也要有网格)
# 单个工作表要渲染的格子数上限(row × column)。Luckysheet 是全量渲染, 不是虚拟滚动:
# 实测某清单表 ws.max_column 报 80 列而实际只用到 13 列, 凭空多渲染 1.6 万个空格子,
# 浏览器直接卡在初始化。超过该上限就不渲染表格, 回退 PDF。
# 6 千是实测下来"稳定不卡"的保守水位: 超了宁可走 PDF, 也不能让用户卡在"渲染中"。
MAX_GRID_CELLS = 6000


_MODE_SUFFIX = {"simple": "_simple", "expand": "_x"}


def sheet_path(file_key: str, mode: str = "") -> str:
    """渲染结果路径。mode: simple=降级用的简易表格, expand=展开隐藏行列的版本。"""
    return os.path.join(SHEET_DIR, file_key + _MODE_SUFFIX.get(mode, "") + ".html")


def sheet_ready(file_key: str) -> bool:
    return os.path.exists(sheet_path(file_key))


def snap_path(file_key: str) -> str:
    """Univer 表格快照（.univsnap.json）。

    表格的正式通道是 /univer，它只认这份快照；.html 只是轻量降级视图。
    被判"大表"的文件现在会跳过 HTML 直接出快照，所以**就绪判断必须同时认快照**，
    否则用户打开时会因为"没有 html"被当成未就绪，一直卡在等待页。
    """
    return os.path.join(SHEET_DIR, (file_key or "") + ".univsnap.json")


def snap_ready(file_key: str) -> bool:
    try:
        return os.path.getsize(snap_path(file_key)) > 0
    except OSError:
        return False


def local_pdf_path(file_key: str) -> str:
    """网关自己用 LibreOffice 转出来的 PDF。"""
    return os.path.join(SHEET_DIR, (file_key or "") + ".pdf")


def local_pdf_ready(file_key: str) -> bool:
    return os.path.exists(local_pdf_path(file_key))


def watermark_bg_url() -> str:
    """水印平铺背景图(data URI), 预览页与 pdf.js viewer 共用。"""
    txt = config.WATERMARK_TXT.strip() or "内部资料"
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' width='420' height='150'>"
           "<text x='20' y='80' font-family='Microsoft YaHei' font-size='20' "
           "fill='#000' fill-opacity='%s' transform='rotate(-10 20 80)'>%s</text></svg>"
           % (config.WATERMARK_ALPHA, html.escape(txt)))
    return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg, safe="")


def _watermark_css() -> str:
    """用 SVG 平铺水印(与 kkFileView 页面层水印保持一致)。"""
    return ("#wm{position:fixed;inset:0;pointer-events:none;z-index:9999;"
            "background-image:url(\"%s\");background-repeat:repeat}"
            % watermark_bg_url())


CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:'Microsoft YaHei',Arial,sans-serif;font-size:13px;background:#fff;color:#222}
.tabs{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #e3e6ea;padding:8px 12px;
 display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.tabs b{font-size:13px;margin-right:6px}
.tab{border:1px solid #d9dde3;background:#f7f8fa;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12px}
.tab.on{background:#2f6fed;color:#fff;border-color:#2f6fed}
.meta{color:#8a9099;font-size:12px;margin-left:auto}
.wrap{overflow:auto;max-height:calc(100vh - 46px)}
table{border-collapse:collapse;table-layout:fixed}
th,td{border:1px solid #dfe3e8;padding:3px 6px;height:22px;overflow:hidden;
 white-space:nowrap;text-overflow:ellipsis;background:#fff;vertical-align:middle}
th{background:#f2f5f9;font-weight:600;position:sticky;top:0;z-index:2}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.c{text-align:center}
td.b{font-weight:600}
tr:nth-child(even) td{background:#fcfcfd}
.tip{padding:10px 14px;color:#8a9099;font-size:12px}
"""

JS = """
document.querySelectorAll('.tab').forEach(function(t){
  t.onclick = function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('on')});
    document.querySelectorAll('.sheet').forEach(function(s){s.style.display='none'});
    t.classList.add('on');
    var el = document.getElementById('s' + t.dataset.i);
    if (el) el.style.display = 'block';
  };
});
"""


def _fmt(value, number_format: str = "") -> tuple:
    """返回 (显示文本, css类)"""
    css = ""
    if value is None:
        return "", ""
    if isinstance(value, bool):
        return ("是" if value else "否"), "c"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            value = int(value)
        nf = (number_format or "").lower()
        if "%" in nf:
            return ("%.1f%%" % (value * 100) if abs(value) <= 10 else "%.0f%%" % (value * 100)), "n"
        return str(value), "n"
    if isinstance(value, (datetime.datetime, datetime.date)):
        if isinstance(value, datetime.datetime) and value.time() == datetime.time(0, 0):
            return value.strftime("%Y-%m-%d"), "c"
        return value.strftime("%Y-%m-%d %H:%M"), "c"
    text = html.escape(str(value)).replace("\n", "<br>")
    return text, ""


LUCKY_TPL = """<html><head><meta charset='utf-8'><title>{name}</title>
<link rel='stylesheet' href='/vendor/luckysheet/plugins/css/pluginsCss.css'>
<link rel='stylesheet' href='/vendor/luckysheet/css/luckysheet.css'>
<style>html,body{{margin:0;height:100%;background:#fff}}
#luckysheet{{position:absolute;inset:0}}
{wm}
.tip{{padding:8px 12px;color:#8a9099;font-size:12px}}</style></head>
<body><div id='luckysheet'></div>{tip}<div id='wm'></div>
<div id='loading' style='position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
 background:#fff;z-index:100;font-size:13px;color:#8a9099'>
 <div style='text-align:center;line-height:2'>
   <div>正在加载表格组件…</div>
   <div id='loadTip' style='font-size:12px;color:#b0b6bd'></div>
   <a id='btnPdf' href='#' style='color:#4a7cf7;text-decoration:none'>
     表格迟迟未出现？点此改用 PDF 模式查看</a>
 </div>
</div>
<script src='/vendor/luckysheet/plugins/js/plugin.js'></script>
<script src='/vendor/luckysheet/luckysheet.umd.js'></script>
<script>
var DATA = {data};
var done = false;
// 降级目标: 交给引擎转 PDF 预览(引擎侧渲染稳定, 不依赖前端组件)
function pdfUrl() {{
  var kkt = (location.search.match(/kkt=([^&]+)/) || [])[1] || '';
  return location.pathname + '?kkt=' + encodeURIComponent(kkt) + '&pdf=1&fb=1';
}}
document.getElementById('btnPdf').href = pdfUrl();
function hideLoading() {{
  done = true;
  var el = document.getElementById('loading');
  if (el && el.parentNode) el.parentNode.removeChild(el);
}}
function fallback(msg) {{
  if (done) return;
  done = true;
  var el = document.getElementById('loading');
  if (el && el.parentNode) el.parentNode.removeChild(el);
  document.getElementById('luckysheet').innerHTML =
    '<div class="tip">' + msg + '，正在改用 PDF 模式重新打开…</div>';
  setTimeout(function () {{ location.href = pdfUrl(); }}, 600);
}}
// 资源加载失败(如网络/缓存异常)也要能降级
window.addEventListener('error', function (e) {{
  if (!done && e && e.target && e.target.tagName === 'SCRIPT') {{
    fallback('表格组件脚本加载失败');
  }}
}}, true);
// 看门狗: 组件初始化迟迟不结束就降级(覆盖脚本加载慢、异步失败等场景)
setTimeout(function () {{
  if (!done) {{
    var t = document.getElementById('loadTip');
    if (t) t.textContent = '仍在初始化，可点击下面链接改用 PDF 模式查看…';
  }}
}}, 6000);
// 组件初始化是同步的, 会占住主线程; 先让遮罩绘制出来, 再初始化, 用户才有可点的降级入口
setTimeout(function () {{
try {{
  if (typeof luckysheet === 'undefined' || typeof luckysheet.create !== 'function') {{
    throw new Error('luckysheet 组件未正确加载');
  }}
  luckysheet.create({{
    container: 'luckysheet', data: DATA, lang: 'zh',
    showinfobar: false, showtoolbar: true, showtoolbarConfig: {{
      undoRedo: false, paintFormat: false, currency: false, percentage: false,
      font: false, fontSize: false, bold: false, italic: false, strike: false,
      textColor: false, fillColor: false, border: false, mergeCell: false,
      horizontalAlignMode: false, verticalAlignMode: false, textWrapMode: false,
      textRotateMode: false, image: false, link: false, chart: false, postil: false,
      pivotTable: false, function: false, frozenMode: true, sortAndFilter: true,
      conditionFormat: false, splitColumn: false, screenshot: false, findAndReplace: false,
      protection: false, print: false, exportXlsx: false
    }},
    showsheetbar: true, showstatisticArea: false, allowEdit: false,
    enableAddRow: false, enableAddCol: false, enablePage: false,
    sheetFormulaBar: false, defaultColWidth: 90, defaultRowHeight: 22
  }});
  hideLoading();
}} catch (e) {{
  fallback('表格组件加载失败: ' + e.message);
}}
}}, 30);
</script></body></html>"""


def _rgb(color) -> str:
    """openpyxl 颜色 -> #RRGGBB, 取不到返回空串"""
    try:
        v = color.rgb if hasattr(color, "rgb") else color
        v = str(v)
        if len(v) == 8:
            v = v[2:]
        if len(v) == 6 and v != "000000":
            return "#" + v
    except Exception:
        pass
    return ""


_ALIGN_H = {"center": 0, "centre": 0, "left": 1, "right": 2, "general": 1}
_ALIGN_V = {"center": 0, "centre": 0, "top": 1, "bottom": 2}


def _lucky_cell(cell):
    """openpyxl 单元格 -> Luckysheet 单元格对象(空单元格返回 None)"""
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    d = {}
    if isinstance(value, bool):
        d["v"] = "TRUE" if value else "FALSE"
        d["m"] = "是" if value else "否"
        d["ct"] = {"fa": "General", "t": "s"}
    elif isinstance(value, (int, float)):
        nf = (cell.number_format or "").lower()
        if "%" in nf:
            d["m"] = "%.1f%%" % (value * 100)
        elif isinstance(value, float) and value != int(value):
            d["m"] = ("%.6f" % value).rstrip("0").rstrip(".")
        else:
            d["m"] = str(int(value) if isinstance(value, float) else value)
        d["v"] = d["m"]
        d["ct"] = {"fa": "General", "t": "n"}
        d["ht"] = 2
    elif isinstance(value, (datetime.datetime, datetime.date)):
        d["m"] = value.strftime("%Y-%m-%d %H:%M") if isinstance(value, datetime.datetime) \
            and value.time() != datetime.time(0, 0) else value.strftime("%Y-%m-%d")
        d["v"] = d["m"]
        d["ct"] = {"fa": "yyyy-MM-dd", "t": "d"}
        d["ht"] = 0
    else:
        text = str(value)
        d["v"] = text
        d["m"] = text
        d["ct"] = {"fa": "General", "t": "s"}
    try:
        font = cell.font
        if font:
            if font.b:
                d["bl"] = 1
            if font.i:
                d["it"] = 1
            if font.u:
                d["un"] = 1
            c = _rgb(font.color) if font.color else ""
            if c:
                d["fc"] = c
            if font.sz:
                d["fs"] = int(font.sz)
    except Exception:
        pass
    try:
        fill = cell.fill
        if fill is not None:
            c = _rgb(fill.fgColor)
            if c:
                d["bg"] = c
    except Exception:
        pass
    try:
        al = cell.alignment
        if al:
            if al.horizontal:
                d["ht"] = _ALIGN_H.get(str(al.horizontal).lower(), d.get("ht", 1))
            if al.vertical:
                d["vt"] = _ALIGN_V.get(str(al.vertical).lower(), 0)
            if al.wrap_text:
                d["tb"] = 2
    except Exception:
        pass
    return d


def _workbook_to_lucky(wb, name: str, stream: bool = False) -> str:
    """openpyxl 工作簿 -> Luckysheet 数据(JSON 字符串)。

    stream=True: 流式读取(大文件), 只读前 MAX_ROWS 行 × MAX_COLS 列, 不做全量解析,
    代价是拿不到合并单元格信息。
    """
    sheets = []
    truncated = False
    total_cells = 0
    for idx, ws in enumerate(wb.worksheets):
        if idx >= MAX_SHEETS:                      # 工作表太多, 之后的不渲染
            truncated = True
            break
        celldata = []
        merge, collen, rowlen = {}, {}, {}
        if stream:
            from itertools import islice
            rows = list(islice(ws.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS), MAX_ROWS))
            max_row = len(rows)
            max_col = max((len(r) for r in rows), default=0)
            truncated = True
            for r, row in enumerate(rows):
                for c, cell in enumerate(row[:MAX_COLS]):
                    obj = _lucky_cell(cell)
                    if obj:
                        celldata.append({"r": r, "c": c, "v": obj})
            try:
                for c in range(1, max_col + 1):
                    dim = ws.column_dimensions.get(get_column_letter(c))
                    if dim and dim.width:
                        collen[str(c - 1)] = _col_width(dim.width)
            except Exception:
                pass
        else:
            max_row = min(ws.max_row or 0, MAX_ROWS)
            max_col = min(ws.max_column or 0, MAX_COLS)
            if (ws.max_row or 0) > MAX_ROWS or (ws.max_column or 0) > MAX_COLS:
                truncated = True
            for r in range(max_row):
                for c in range(max_col):
                    obj = _lucky_cell(ws.cell(row=r + 1, column=c + 1))
                    if obj:
                        celldata.append({"r": r, "c": c, "v": obj})
        merge, collen, rowlen = {}, {}, {}
        try:
            for rng in ws.merged_cells.ranges:
                if len(merge) >= MAX_MERGE_PER_SHEET:
                    break
                r0, c0 = rng.min_row - 1, rng.min_col - 1
                if r0 >= max_row or c0 >= max_col:
                    continue                        # 合并区起点已在渲染范围外
                rs = min(rng.max_row, max_row) - rng.min_row + 1
                cs = min(rng.max_col, max_col) - rng.min_col + 1
                if rs < 1 or cs < 1:
                    continue
                if rs == 1 and cs == 1:
                    continue                        # 1x1 不是真正的合并
                merge["%d_%d" % (r0, c0)] = {"r": r0, "c": c0, "rs": rs, "cs": cs}
        except Exception:
            pass
        # 行列宽只在"完整模式"下有；read_only 的 ReadOnlyWorksheet 没有这两个
        # 属性（访问直接抛 AttributeError）。流式表本来就只求"能看能滚"，
        # 没有列宽就用默认值，不必因此整份失败 —— 实测一份 17.9MB 的作业指导书
        # 就在这一行炸掉，整条表格通道退化成 PDF。
        cdims = getattr(ws, "column_dimensions", None) or {}
        rdims = getattr(ws, "row_dimensions", None) or {}
        for c in range(1, max_col + 1):
            dim = cdims.get(get_column_letter(c)) if cdims else None
            collen[str(c - 1)] = _col_width(dim.width if dim else None)
        for r in range(1, max_row + 1):
            dim = rdims.get(r) if rdims else None
            if dim and dim.height:
                rowlen[str(r - 1)] = max(18, min(180, int(dim.height * 1.33)))
        frozen = {}
        try:
            fp = ws.freeze_panes
            if fp and isinstance(fp, str):
                from openpyxl.utils import column_index_from_string
                import re as _re
                m = _re.match(r"([A-Z]+)(\d+)", fp)
                if m:
                    col = column_index_from_string(m.group(1)) - 1
                    row = int(m.group(2)) - 1
                    if row > 0 and col > 0:
                        frozen = {"type": "both",
                                  "range": {"row_focus": row - 1, "column_focus": col - 1}}
                    elif row > 0:
                        frozen = {"type": "row", "range": {"row_focus": row - 1}}
                    elif col > 0:
                        frozen = {"type": "column", "range": {"column_focus": col - 1}}
        except Exception:
            pass
        # 累计单元格数超上限: 停止渲染后续工作表(已渲染的保留)
        total_cells += len(celldata)
        if total_cells > MAX_TOTAL_CELLS:
            truncated = True
        # row / column 是 Luckysheet 的必需字段, 缺失会导致初始化异常(表现为一直"渲染中")
        # 关键: 按**实际有数据**的范围算, 不能用 ws.max_row/max_column ——
        # 后者常被单元格格式、空行样式撑大(实测声明 80 列而实际只用 13 列),
        # 多出来的空白格子全都要渲染成 DOM, 浏览器会直接卡死。
        used_row = max((c["r"] for c in celldata), default=-1) + 1
        used_col = max((c["c"] for c in celldata), default=-1) + 1
        # 合并区域的右下角是空 MergedCell, 不在 celldata 里, 要单独算进来
        for _m in merge.values():
            used_row = max(used_row, _m["r"] + _m["rs"])
            used_col = max(used_col, _m["c"] + _m["cs"])
        grid_row = max(min(used_row, max_row), MIN_ROWS)
        grid_col = max(min(used_col, max_col), MIN_COLS)
        if grid_row * grid_col > MAX_GRID_CELLS:
            raise SheetTooHeavy("工作表「%s」需渲染 %d 个格子(%d×%d), 超过上限 %d, 改用 PDF 模式"
                                % (ws.title, grid_row * grid_col, grid_row, grid_col,
                                   MAX_GRID_CELLS))
        sheets.append({
            "name": ws.title, "index": idx, "order": idx, "status": 1,
            "row": grid_row, "column": grid_col,
            "celldata": celldata,
            "config": {"merge": merge, "columnlen": collen, "rowlen": rowlen,
                       "frozen": frozen, "rowhidden": {}, "colhidden": {}},
            "defaultColWidth": 90, "defaultRowHeight": 22,
        })
        if total_cells > MAX_TOTAL_CELLS:
            break
    tip = ('<div class="tip">为保证打开速度，最多渲染 %d 个工作表，'
           '每个工作表 %d 行 / %d 列。</div>'
           % (MAX_SHEETS, MAX_ROWS, MAX_COLS)) if truncated else ""
    payload = json.dumps(sheets, ensure_ascii=False).replace("</", "<\\/")
    return LUCKY_TPL.format(name=html.escape(name), data=payload,
                            wm=_watermark_css(), tip=tip)


def _col_width(width) -> int:
    try:
        w = float(width or 0)
    except (TypeError, ValueError):
        w = 0
    if not w:
        return 90
    return max(46, min(420, int(w * 7.5)))


def _workbook_to_html(wb, name: str) -> str:
    parts = []
    truncated = False
    for idx, ws in enumerate(wb.worksheets):
        rows = []
        max_row = min(ws.max_row or 0, MAX_ROWS)
        max_col = min(ws.max_column or 0, MAX_COLS)
        if (ws.max_row or 0) > MAX_ROWS or (ws.max_column or 0) > MAX_COLS:
            truncated = True
        merged = {}
        try:
            for rng in ws.merged_cells.ranges:
                merged[(rng.min_row, rng.min_col)] = (rng.max_row - rng.min_row + 1,
                                                      rng.max_col - rng.min_col + 1)
        except Exception:
            merged = {}
        cols = []
        cdims2 = getattr(ws, "column_dimensions", None) or {}
        for c in range(1, max_col + 1):
            # 注意: 合并后的单元格是 MergedCell, 没有 column_letter 属性, 用工具函数换算
            # read_only 模式没有 column_dimensions（见 _workbook_to_lucky 的说明）
            dim = cdims2.get(get_column_letter(c)) if cdims2 else None
            cols.append(_col_width(dim.width if dim else None))
        total = sum(cols) + 20
        body = []
        for r in range(1, max_row + 1):
            cells = []
            c = 1
            while c <= max_col:
                cell = ws.cell(row=r, column=c)
                rowspan, colspan = merged.get((r, c), (1, 1))
                style_parts = ["width:%dpx" % cols[c - 1]]
                text, cls = _fmt(cell.value, cell.number_format)
                try:
                    if cell.font and cell.font.b:
                        cls += " b"
                except Exception:
                    pass
                try:
                    fill = cell.fill
                    if fill and fill.fgColor and fill.fgColor.rgb and \
                            str(fill.fgColor.rgb) not in ("00000000", "0"):
                        style_parts.append("background:#%s" % str(fill.fgColor.rgb)[-6:])
                except Exception:
                    pass
                tag = "th" if r == 1 else "td"
                attr = ' class="%s" style="%s"' % (cls.strip(), ";".join(style_parts))
                if rowspan > 1:
                    attr += ' rowspan="%d"' % rowspan
                if colspan > 1:
                    attr += ' colspan="%d"' % colspan
                cells.append("<%s%s>%s</%s>" % (tag, attr, text, tag))
                c += colspan if colspan > 1 else 1
            body.append("<tr>%s</tr>" % "".join(cells))
        rows.append(
            '<div class="sheet" id="s%d" style="%s"><div class="wrap">'
            '<table style="width:%dpx"><tbody>%s</tbody></table></div></div>'
            % (idx, "display:block" if idx == 0 else "display:none", total, "".join(body)))
        parts.append((ws.title, "".join(rows)))

    tabs = "".join(
        '<span class="tab%s" data-i="%d">%s</span>' % (" on" if i == 0 else "", i, html.escape(t))
        for i, (t, _) in enumerate(parts))
    sheets = "".join(s for _, s in parts)
    tip = ('<div class="tip">为保证打开速度，每个工作表最多渲染 %d 行 / %d 列，'
           '超出的内容未显示（原文件完整内容不受影响）。</div>' % (MAX_ROWS, MAX_COLS)) if truncated else ""
    return ("<html><head><meta charset='utf-8'><title>%s</title><style>%s%s</style></head>"
            "<body><div class='tabs'><b>%s</b>%s<span class='meta'>表格模式 · 共 %d 个工作表</span></div>"
            "%s%s<div id='wm'></div><script>%s</script></body></html>"
            % (html.escape(name), CSS, _watermark_css(), html.escape(name), tabs,
               len(parts), sheets, tip, JS))


def _csv_to_html(data: bytes, name: str) -> str:
    # Excel「另存为 CSV」在中文 Windows 上默认写 GBK，不能只按 UTF-8 解，
    # 否则整张表变成一串 U+FFFD。与 markdown 那边保持同一套兜底。
    text = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", "replace")
    reader = csv.reader(io.StringIO(text))
    rows = []
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        cells = "".join("<td>%s</td>" % html.escape(v) for v in row[:MAX_COLS])
        rows.append("<tr>%s</tr>" % cells)
    return ("<html><head><meta charset='utf-8'><title>%s</title><style>%s%s</style></head>"
            "<body><div class='tabs'><b>%s</b><span class='meta'>CSV 表格模式</span></div>"
            "<div class='wrap'><table><tbody>%s</tbody></table></div>"
            "<div id='wm'></div></body></html>"
            % (html.escape(name), CSS, _watermark_css(), html.escape(name), "".join(rows)))


SVG_JS = ""


def _svg_to_html(data: bytes, name: str) -> str:
    """把 SVG 直接内嵌到页面里渲染(矢量, 放大不失真)。"""
    text = data.decode("utf-8", "replace")
    low = text.lower()
    start = low.find("<svg")
    if start < 0:
        raise RuntimeError("文件内容不是有效的 SVG")
    svg = text[start:]
    # 基本安全过滤: 去掉脚本与事件属性, 避免 SVG 携带可执行代码
    svg = re.sub(r"(?is)<\s*script.*?<\s*/\s*script\s*>", "", svg)
    svg = re.sub(r"(?i)\son\w+\s*=\s*\"[^\"]*\"", "", svg)
    svg = re.sub(r"(?i)\son\w+\s*=\s*'[^']*'", "", svg)
    svg = re.sub(r"(?i)\son\w+\s*=\s*[^\s>]+", "", svg)
    return ("<html><head><meta charset='utf-8'><title>%s</title><style>%s%s"
            ".svgbx{padding:16px;display:flex;justify-content:center;background:#fff}"
            ".svgbx svg{max-width:100%%;height:auto}</style></head>"
            "<body><div class='tabs'><b>%s</b><span class='meta'>SVG 矢量预览</span></div>"
            "<div class='svgbx'>%s</div><div id='wm'></div></body></html>"
            % (html.escape(name), CSS, _watermark_css(), html.escape(name), svg))


def _parse_volume_mb(data: bytes, ext: str):
    """估算"要解析多少数据"（MB），返回 (体积, 口径名)。

    只对 xlsx/xlsm 有效：读 zip 目录拿各 sheet XML 与 sharedStrings 的
    **未压缩**大小（openpyxl 的遍历量正比于它）。取不到就退回文件体积。
    这样"图片很多、单元格很少"的作业指导书不会被误判成大表。
    """
    ext = (ext or "").lower()
    if ext not in ("xlsx", "xlsm"):
        return len(data) / 1048576, "源文件"
    try:
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(data))
        total = 0
        for i in z.infolist():
            n = i.filename
            if (n.startswith("xl/worksheets/sheet") and n.endswith(".xml")) \
                    or n == "xl/sharedStrings.xml":
                total += i.file_size
        if total > 0:
            return total / 1048576, "表格数据"
    except Exception:
        pass
    return len(data) / 1048576, "源文件"


def _render_workbook(data: bytes, name: str, stream: bool) -> str:
    """同步渲染工作簿(在线程池里跑, 便于加超时)。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=stream)
    try:
        if config.SHEET_RENDER == "lucky":
            return _workbook_to_lucky(wb, name, stream=stream)
        return _workbook_to_html(wb, name)
    finally:
        wb.close()


async def build(file_url: str, file_key: str, file_name: str, ext: str) -> str:
    """下载表格文件并渲染成 HTML, 返回生成文件路径。"""
    import openpyxl
    os.makedirs(SHEET_DIR, exist_ok=True)
    client = await engine.http()
    resp = await client.get(file_url, timeout=180)
    if resp.status_code >= 400:
        raise RuntimeError("下载文件失败 HTTP %s" % resp.status_code)
    data = resp.content
    # --- 预检 1: 体积。判"大表"要看**要解析多少数据**, 不是文件多大 ---
    # 原来直接拿 len(data) 比阈值，遇到"文档型 xlsx"就误杀：这类表是作业指导书，
    # 塞了二三十张现场照片（实测 17.9MB 里 17.8MB 是 xl/media/*.jpeg），
    # 单元格只有 82 个。按体积判定会把它推给 LibreOffice 转 PDF ——
    # 实测 85 秒，而 openpyxl 建快照只要 0.47 秒。
    # 现在 xlsx/xlsm 改看"表格 XML + sharedStrings 的未压缩体积"，
    # 那才是 openpyxl 真正要遍历的东西；zip 目录读起来是毫秒级。
    size_mb = len(data) / 1048576
    if size_mb > MAX_FILE_MB:
        raise SheetTooHeavy("源文件 %.1fMB 超过硬上限 %sMB, 改用 PDF 模式"
                            % (size_mb, MAX_FILE_MB))
    heavy_mb, basis = _parse_volume_mb(data, ext)
    if heavy_mb > config.SHEET_DIRECT_MAX_MB:
        raise SheetTooHeavy("%s %.1fMB 超过 %sMB, 改用 PDF 模式"
                            % (basis, heavy_mb, config.SHEET_DIRECT_MAX_MB))
    if ext in config.SVG_EXTS:
        page = _svg_to_html(data, file_name)
    elif ext == "csv":
        page = _csv_to_html(data, file_name)
    else:
        # 解析放到线程池并限时: 大文件全量解析是分钟级的, 这里改成流式 + 限时
        # 流式同样按"解析量"判：图片型 xlsx 有 17.9MB 但只要遍历 82 个单元格，
        # 按文件体积切流式反而会踩 read_only 的坑（见 _col_widths 的说明）。
        stream = heavy_mb > STREAM_MB
        loop = asyncio.get_event_loop()
        try:
            page = await asyncio.wait_for(
                loop.run_in_executor(None, _render_workbook, data, file_name, stream),
                timeout=config.SHEET_MAX_BUILD_SEC)
        except asyncio.TimeoutError:
            # --- 预检 2: 解析耗时。解析都这么久, 组件初始化只会更慢 ---
            raise SheetTooHeavy("解析超过 %ss, 改用 PDF 模式"
                                % config.SHEET_MAX_BUILD_SEC)
    # --- 预检 3: 渲染页面体积。越大浏览器解析 JSON + 组件初始化越慢 ---
    page_mb = len(page.encode("utf-8")) / 1048576
    if page_mb > config.SHEET_MAX_PAGE_MB:
        raise SheetTooHeavy("渲染页面 %.1fMB 超过 %sMB, 改用 PDF 模式"
                            % (page_mb, config.SHEET_MAX_PAGE_MB))
    # 生成一份简易表格备份, 供 ?simple=1 手动降级使用(超限的已在上面回退, 这里都是轻量的)
    if ext not in config.SVG_EXTS and config.SHEET_RENDER == "lucky":
        try:
            loop = asyncio.get_event_loop()
            alt = await asyncio.wait_for(
                loop.run_in_executor(None, _render_simple, data, file_name),
                timeout=max(5, config.SHEET_MAX_BUILD_SEC))
            with open(sheet_path(file_key, "simple"), "w", encoding="utf-8") as af:
                af.write(alt)
        except Exception:
            pass
    path = sheet_path(file_key)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path


def _render_simple(data: bytes, file_name: str) -> str:
    """渲染简易表格(纯 HTML, 不依赖前端组件), 供手动降级使用。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    try:
        return _workbook_to_html(wb, file_name)
    finally:
        wb.close()
