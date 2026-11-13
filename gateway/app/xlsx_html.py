"""xlsx → 高保真 HTML 表格(不经过 PDF)。

目标: 尽可能接近本地 Excel 打开的效果, 同时 2 秒内出图。

为此解析并还原:
  - 隐藏行/列           (原实现会把隐藏列也画出来, 又慢又不像)
  - 字体: 名称/字号/粗体/斜体/下划线/颜色(含主题色 + tint)
  - 行高(单行 ht / 默认 defaultRowHeight)、列宽(按默认字体宽度换算)
  - 边框(四边, thin/medium/thick/dashed/dotted/double)
  - 对齐(水平 + 垂直 + 自动换行)
  - 合并单元格、冻结窗格(前 N 行/N 列吸顶)
  - 条件格式 cellIs(等于/大于/介于/包含文本等) + dxf 配色
  - 浮动图片(twoCellAnchor / oneCellAnchor), 按锚点绝对定位
  - 数字格式(日期/时间/百分比/千分位/小数位)

不做的: 图表、数据透视、宏、复杂形状(只渲染图片)。
"""
import datetime
import html
import io
import posixpath
import re
import zipfile

from . import symbol_fonts
import xml.etree.ElementTree as ET

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
TAG = "{%s}" % NS
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
TAG_XDR = "{%s}" % NS_XDR
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"

DATE_FMT_IDS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
EPOCH = datetime.datetime(1899, 12, 30)

BORDER_STYLE = {
    "thin": "1px solid", "medium": "2px solid", "thick": "3px solid",
    "dashed": "1px dashed", "dotted": "1px dotted", "double": "3px double",
    "hair": "1px solid", "mediumDashed": "2px dashed", "dashDot": "1px dashed",
    "mediumDashDot": "2px dashed", "dashDotDot": "1px dashed",
    "mediumDashDotDot": "2px dashed", "slantDashDot": "1px dashed",
}

EMU_PER_PX = 9525
HEADER_H = 22          # 列号表头高度
ROWNO_W = 46           # 行号列宽

# 连续多少列全空就折叠成一个"省略列"。设小会丢网格线, 设大则省不下节点。
# 实测某表 16383 列里只有 19 列有内容, 折叠后 19 项即可。
GAP_COL_MIN = 12
GAP_COL_WIDTH = 56     # 省略列宽度: 要放得下两行小字(⋯ 与 列范围)

# 单个工作表最多渲染多少个单元格。超过就截断并在表尾明确提示,
# 保证任何文件都不会把浏览器拖死(实测业务表最大约 1.7 万, 留 2 倍余量)。
BODY_CELL_BUDGET = 40000


# ------------------------------------------------------------------ 基础工具
def _text_of(node):
    """共享字符串/富文本 → 纯文本。

    富文本里如果某个 run 用了符号字体(Wingdings 等)，就把它换成 Unicode：
    浏览器没有这些字体，原样输出就是 "¨ ÿ" 这类乱码（勾选框最常见）。
    见 symbol_fonts.py 的说明。
    """
    if node is None:
        return ""
    runs = node.findall(TAG + "r")
    if not runs:
        return "".join(t.text or "" for t in node.iter(TAG + "t"))
    parts = []
    for r in runs:
        rf = r.find("%srPr/%srFont" % (TAG, TAG))
        font = rf.get("val") if rf is not None else ""
        txt = "".join(t.text or "" for t in r.iter(TAG + "t"))
        parts.append(symbol_fonts.translate(txt, font))
    return "".join(parts)


def _col_to_idx(ref):
    m = re.match(r"([A-Z]+)(\d+)", ref or "")
    if not m:
        return 0, 0
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)) - 1, col - 1


def _idx_to_col(idx):
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _hex(color):
    if not color:
        return ""
    c = color.lstrip("#")
    if len(c) == 8:
        c = c[2:]
    if len(c) != 6 or not re.match(r"^[0-9a-fA-F]{6}$", c):
        return ""
    return "#" + c.lower()


def _tint(hex_color, tint):
    if not hex_color or not tint:
        return hex_color
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    if tint > 0:
        r = round(r + (255 - r) * tint)
        g = round(g + (255 - g) * tint)
        b = round(b + (255 - b) * tint)
    else:
        r = round(r * (1 + tint))
        g = round(g * (1 + tint))
        b = round(b * (1 + tint))
    clamp = lambda x: max(0, min(255, x))
    return "#%02x%02x%02x" % (clamp(r), clamp(g), clamp(b))


def _theme_palette(z):
    try:
        root = ET.fromstring(z.read("xl/theme/theme1.xml"))
    except KeyError:
        return []
    scheme = root.find(".//{%s}clrScheme" % NS_A)
    if scheme is None:
        return []
    # 注意顺序: xlsx 的 theme 索引是 **0=lt1(背景1/白), 1=dk1(文本1/黑), 2=lt2, 3=dk2**,
    # 4~9=accent1~6, 10=hlink, 11=folHlink。
    # 不能直接用 clrScheme 里的 dk1/lt1 顺序 —— 那样 theme=0 会取到黑色,
    # 整片格子被涂黑(实测 APQP 有 785 个)。
    order = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3",
             "accent4", "accent5", "accent6", "hlink", "folHlink"]
    out = []
    for name in order:
        node = scheme.find("{%s}%s" % (NS_A, name))
        color = ""
        if node is not None:
            for child in node:
                if child.tag.endswith("srgbClr"):
                    color = _hex(child.get("val"))
                elif child.tag.endswith("sysClr"):
                    color = _hex(child.get("lastClr"))
                if color:
                    break
        out.append(color or "#000000")
    return out


def _color(node, theme):
    if node is None:
        return ""
    rgb = _hex(node.get("rgb"))
    if rgb:
        base = rgb
    else:
        idx = node.get("theme")
        if idx is None or not theme:
            return ""
        try:
            base = theme[int(idx)]
        except (ValueError, IndexError):
            return ""
    try:
        t = float(node.get("tint")) if node.get("tint") is not None else 0.0
    except ValueError:
        t = 0.0
    return _tint(base, t)


def _luma(hex_color):
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return 255
    return 0.299 * r + 0.587 * g + 0.114 * b


def _contrast(bg):
    """深底给白字, 浅底给黑字。"""
    return "#ffffff" if _luma(bg) < 140 else "#000000"


# ------------------------------------------------------------------ 样式
def _load_styles(z, theme):
    try:
        root = ET.fromstring(z.read("xl/styles.xml"))
    except KeyError:
        return {"fonts": [], "fills": [], "borders": [], "xfs": [], "dxfs": []}

    fonts = []
    for f in root.findall("%sfonts/%sfont" % (TAG, TAG)):
        name = f.find(TAG + "name")
        size = f.find(TAG + "sz")
        try:
            pt = float(size.get("val")) if size is not None else 11.0
        except (TypeError, ValueError):
            pt = 11.0
        fonts.append({
            "name": name.get("val") if name is not None else "Calibri",
            "size": pt,
            "bold": f.find(TAG + "b") is not None,
            "italic": f.find(TAG + "i") is not None,
            "underline": f.find(TAG + "u") is not None,
            "color": _color(f.find(TAG + "color"), theme),
        })

    fills = []
    for fl in root.findall("%sfills/%sfill" % (TAG, TAG)):
        pf = fl.find(TAG + "patternFill")
        color = ""
        if pf is not None:
            color = _color(pf.find(TAG + "fgColor"), theme) or ""
        fills.append(color)

    borders = []
    for b in root.findall("%sborders/%sborder" % (TAG, TAG)):
        sides = {}
        for side in ("left", "right", "top", "bottom"):
            node = b.find(TAG + side)
            if node is None or not node.get("style"):
                continue
            css = BORDER_STYLE.get(node.get("style"), "1px solid")
            color = _color(node.find(TAG + "color"), theme) or "#000000"
            sides[side] = css + " " + color
        borders.append(sides)

    dxfs = []
    dxf_root = root.find("%sdxfs" % TAG)
    if dxf_root is not None:
        for d in dxf_root.findall(TAG + "dxf"):
            fill = ""
            f_node = d.find("%sfill/%spatternFill" % (TAG, TAG))
            if f_node is not None:
                fill = (_color(f_node.find(TAG + "bgColor"), theme)
                        or _color(f_node.find(TAG + "fgColor"), theme))
            font_node = d.find(TAG + "font")
            color = ""
            bold = italic = False
            if font_node is not None:
                color = _color(font_node.find(TAG + "color"), theme)
                bold = font_node.find(TAG + "b") is not None
                italic = font_node.find(TAG + "i") is not None
            dxfs.append({"fill": fill, "color": color, "bold": bold, "italic": italic})

    numfmts = {}
    for nf in root.iter(TAG + "numFmt"):
        try:
            numfmts[int(nf.get("numFmtId"))] = nf.get("formatCode") or ""
        except (TypeError, ValueError):
            pass

    xfs = []
    xfs_root = root.find("%scellXfs" % TAG)
    if xfs_root is not None:
        for xf in xfs_root.findall(TAG + "xf"):
            try:
                fid = int(xf.get("fontId") or 0)
                flid = int(xf.get("fillId") or 0)
                bid = int(xf.get("borderId") or 0)
                nfid = int(xf.get("numFmtId") or 0)
            except ValueError:
                fid = flid = bid = nfid = 0
            al = xf.find(TAG + "alignment")
            xfs.append({
                "font": fonts[fid] if fid < len(fonts) else (fonts[0] if fonts else None),
                "fill": fills[flid] if flid < len(fills) else "",
                "border": borders[bid] if bid < len(borders) else {},
                "nf": nfid,
                "code": numfmts.get(nfid, ""),
                "h": (al.get("horizontal") or "") if al is not None else "",
                "v": (al.get("vertical") or "") if al is not None else "",
                "wrap": (al.get("wrapText") or "") in ("1", "true") if al is not None else False,
            })
    return {"fonts": fonts, "fills": fills, "borders": borders, "xfs": xfs, "dxfs": dxfs}


# ------------------------------------------------------------------ 数字格式
def _fmt_number(v, code, nfid):
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    try:
        num = float(v)
    except (TypeError, ValueError):
        return str(v)

    low = (code or "").lower()
    is_date = nfid in DATE_FMT_IDS or (low and re.search(r"[ymd]", low)
                                       and "general" not in low
                                       and not re.search(r"[#0]", low.split(";")[0]))
    if is_date and 1 <= num <= 2958465:
        dt = EPOCH + datetime.timedelta(days=num)
        if "h" in low or "s" in low:
            return dt.strftime("%Y-%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d")

    if "%" in low:
        dec = len(re.findall(r"0", low.split(".")[1].split("%")[0])) if "." in low else 0
        return ("%." + str(dec) + "f%%") % (num * 100)

    if abs(num - round(num)) < 1e-9 and "." not in (code or ""):
        n = int(round(num))
        return "{:,}".format(n) if code and "," in code else str(n)

    dec = 2
    if code and "." in code:
        m = re.match(r"[0#]+", code.split(".")[1])
        if m:
            dec = len(m.group(0))
    s = ("%." + str(dec) + "f") % num
    if code and "," in code:
        head, _, frac = s.partition(".")
        try:
            head = "{:,}".format(int(head))
        except ValueError:
            pass
        s = head + ("." + frac if frac else "")
    return s


# ------------------------------------------------------------------ 条件格式
def _cf_rules(root, theme, dxfs):
    rules = []
    for cf in root.findall("%sconditionalFormatting" % TAG):
        ranges = []
        for part in (cf.get("sqref") or "").split():
            if ":" in part:
                a, b = part.split(":", 1)
                r1, c1 = _col_to_idx(a)
                r2, c2 = _col_to_idx(b)
                ranges.append((r1, c1, r2, c2))
            else:
                r, c = _col_to_idx(part)
                ranges.append((r, c, r, c))
        for rule in cf.findall(TAG + "cfRule"):
            try:
                dxf = dxfs[int(rule.get("dxfId"))]
            except (TypeError, ValueError, IndexError):
                dxf = None
            rules.append({
                "ranges": ranges,
                "type": rule.get("type") or "",
                "op": rule.get("operator") or "",
                "text": rule.get("text") or "",
                "formulas": [f.text or "" for f in rule.findall(TAG + "formula")],
                "dxf": dxf,
                "priority": int(rule.get("priority") or 999),
            })
    rules.sort(key=lambda x: x["priority"])
    return rules


def _num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _plain(text):
    """条件格式里的公式/文本常量常带引号: "H" / "已完成" → 去引号。"""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        t = t[1:-1]
    return t.replace('""', '"')


def _cf_match(rule, value, raw):
    typ = rule["type"]
    number = raw if isinstance(raw, (int, float)) else _num(raw)
    fs = [_plain(x) for x in rule["formulas"]]
    op = rule["op"]
    if typ == "cellIs" and fs:
        a = _num(fs[0])
        b = _num(fs[1]) if len(fs) > 1 else None
        if op == "equal":
            return (number is not None and a is not None and number == a) or value == fs[0]
        if op == "notEqual":
            return not ((number is not None and a is not None and number == a) or value == fs[0])
        if number is not None and a is not None:
            if op == "greaterThan":
                return number > a
            if op == "greaterThanOrEqual":
                return number >= a
            if op == "lessThan":
                return number < a
            if op == "lessThanOrEqual":
                return number <= a
            if op == "between" and b is not None:
                return a <= number <= b
            if op == "notBetween" and b is not None:
                return not (a <= number <= b)
    if typ == "containsText":
        return rule["text"] in value if rule["text"] else False
    if typ == "notContainsText":
        return rule["text"] not in value if rule["text"] else True
    if typ == "beginsWith":
        return value.startswith(rule["text"])
    if typ == "endsWith":
        return value.endswith(rule["text"])
    return False


# ------------------------------------------------------------------ 工作表
def _parse_sheet(z, path, shared, styles, theme):
    root = ET.fromstring(z.read(path))

    fmt = root.find(TAG + "sheetFormatPr")
    try:
        default_h = float(fmt.get("defaultRowHeight")) if fmt is not None else 14.25
    except (TypeError, ValueError):
        default_h = 14.25
    try:
        default_w = float(fmt.get("defaultColWidth")) if fmt is not None else 8.43
    except (TypeError, ValueError):
        default_w = 8.43

    cols = {}
    cols_node = root.find(TAG + "cols")
    if cols_node is not None:
        for col in cols_node.findall(TAG + "col"):
            try:
                lo = int(col.get("min")) - 1
                hi = int(col.get("max")) - 1
                w = float(col.get("width") or default_w)
            except (TypeError, ValueError):
                continue
            hidden = (col.get("hidden") or "") in ("1", "true")
            for i in range(lo, min(hi, lo + 500) + 1):
                cols[i] = {"w": w, "hidden": hidden}

    rows, hidden_rows = {}, set()
    max_r = max_c = 0
    for row in root.iter(TAG + "row"):
        try:
            r = int(row.get("r")) - 1
        except (TypeError, ValueError):
            continue
        if (row.get("hidden") or "") in ("1", "true"):
            hidden_rows.add(r)
        height = row.get("ht")
        cells = []
        for c in row.findall(TAG + "c"):
            rr, cc = _col_to_idx(c.get("r") or "")
            if rr != r:
                r = rr
            t = c.get("t") or "n"
            vnode = c.find(TAG + "v")
            raw = vnode.text if vnode is not None else None
            if t == "s":
                try:
                    value = shared[int(raw)]
                except (TypeError, ValueError, IndexError):
                    value = ""
            elif t == "inlineStr":
                value = _text_of(c.find(TAG + "is"))
            elif t == "str":
                value = raw or ""
            elif t == "b":
                value = "TRUE" if raw == "1" else "FALSE"
            elif t == "e":
                value = raw or ""
            else:
                value = raw
            try:
                sidx = int(c.get("s") or 0)
            except ValueError:
                sidx = 0
            st = styles["xfs"][sidx] if sidx < len(styles["xfs"]) else None
            # 整个单元格是符号字体(没有富文本 run)时，同样要换成 Unicode
            if value and st and st.get("font") and symbol_fonts.is_symbol_font(
                    st["font"].get("name")):
                value = symbol_fonts.translate(value, st["font"].get("name"))
            raw_num = None
            if value not in (None, ""):
                if t == "n":
                    try:
                        raw_num = float(value)
                    except (TypeError, ValueError):
                        raw_num = None
                    value = _fmt_number(raw_num, st["code"] if st else "", st["nf"] if st else 0)
                if isinstance(value, float):
                    value = _fmt_number(value, "", 0)
            elif st and not (st["fill"] or st["border"] or (st["font"] or {}).get("bold")):
                continue
            cells.append((cc, value, sidx, raw_num))
            if cc > max_c:
                max_c = cc
        rows[r] = {"c": cells, "h": height}
        if r > max_r:
            max_r = r

    merges = []
    mc = root.find("%smergeCells" % TAG)
    if mc is not None:
        for m in mc.findall(TAG + "mergeCell"):
            ref = m.get("ref") or ""
            if ":" not in ref:
                continue
            a, b = ref.split(":", 1)
            r1, c1 = _col_to_idx(a)
            r2, c2 = _col_to_idx(b)
            merges.append((r1, c1, r2, c2))

    pane = {"x": 0, "y": 0}
    p = root.find("%ssheetViews/%ssheetView/%spane" % (TAG, TAG, TAG))
    if p is not None and (p.get("state") or "") in ("frozen", "frozenSplit"):
        try:
            pane = {"x": int(float(p.get("xSplit") or 0)),
                    "y": int(float(p.get("ySplit") or 0))}
        except ValueError:
            pane = {"x": 0, "y": 0}

    dim = root.find(TAG + "dimension")
    if dim is not None:
        ref = dim.get("ref") or ""
        if ":" in ref:
            a, b = ref.split(":", 1)
            dr, dc = _col_to_idx(b)
            max_r = max(max_r, dr)
            max_c = max(max_c, dc)

    return {"rows": rows, "hidden_rows": hidden_rows, "cols": cols,
            "merges": merges, "pane": pane, "max_row": max_r, "max_col": max_c,
            "default_h": default_h, "default_w": default_w,
            "cf": _cf_rules(root, theme, styles["dxfs"])}


def _parse_drawings(z, sheet_path):
    """返回图片锚点列表(只处理图片, 不处理图表/形状)。"""
    rels_path = posixpath.join(posixpath.dirname(sheet_path), "_rels",
                               posixpath.basename(sheet_path) + ".rels")
    try:
        rroot = ET.fromstring(z.read(rels_path))
    except KeyError:
        return []
    drawing = ""
    for r in rroot:
        if (r.get("Type") or "").endswith("/drawing"):
            drawing = r.get("Target") or ""
            break
    if not drawing:
        return []
    path = posixpath.normpath(posixpath.join(posixpath.dirname(sheet_path), drawing))
    try:
        root = ET.fromstring(z.read(path))
    except KeyError:
        return []
    drels_path = posixpath.join(posixpath.dirname(path), "_rels",
                                posixpath.basename(path) + ".rels")
    rels = {}
    try:
        for r in ET.fromstring(z.read(drels_path)):
            rels[r.get("Id")] = r.get("Target") or ""
    except KeyError:
        pass

    out = []
    for anchor in root:
        tag = anchor.tag.split("}")[-1]
        if tag not in ("twoCellAnchor", "oneCellAnchor"):
            continue
        pic = anchor.find(TAG_XDR + "pic")
        if pic is None:
            continue
        blip = pic.find(".//{%s}blip" % NS_A)
        if blip is None:
            continue
        media = posixpath.basename(rels.get(blip.get("{%s}embed" % NS_R), ""))
        if not media.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg")):
            continue
        frm = anchor.find(TAG_XDR + "from")
        try:
            c1 = int(frm.find(TAG_XDR + "col").text)
            r1 = int(frm.find(TAG_XDR + "row").text)
            ox = int(frm.find(TAG_XDR + "colOff").text or 0) // EMU_PER_PX
            oy = int(frm.find(TAG_XDR + "rowOff").text or 0) // EMU_PER_PX
        except (AttributeError, TypeError, ValueError):
            continue
        item = {"name": media, "c1": c1, "r1": r1, "ox": ox, "oy": oy}
        if tag == "twoCellAnchor":
            to = anchor.find(TAG_XDR + "to")
            try:
                item["c2"] = int(to.find(TAG_XDR + "col").text)
                item["r2"] = int(to.find(TAG_XDR + "row").text)
                item["ox2"] = int(to.find(TAG_XDR + "colOff").text or 0) // EMU_PER_PX
                item["oy2"] = int(to.find(TAG_XDR + "rowOff").text or 0) // EMU_PER_PX
            except (AttributeError, TypeError, ValueError):
                continue
        else:
            ext = anchor.find(TAG_XDR + "ext")
            try:
                item["w"] = int(ext.get("cx")) // EMU_PER_PX
                item["h"] = int(ext.get("cy")) // EMU_PER_PX
            except (AttributeError, TypeError, ValueError):
                continue
        out.append(item)
    return out


# ------------------------------------------------------------------ 渲染
def _col_px(width, base_pt):
    """列宽(字符数) → 像素。Excel 的宽度单位是默认字体下 '0' 的个数。"""
    mdw = max(6.0, round(base_pt * 0.62))
    return max(12, int(round(width * mdw + 5)))


def _row_px(ht, default_h, max_pt):
    if ht:
        try:
            pt = float(ht)
        except (TypeError, ValueError):
            pt = default_h
    else:
        pt = max(default_h, max_pt * 1.28)
    return max(15, int(round(pt * 96 / 72)))


def build(data, file_name, watermark_url="", key="", expand=False):
    """expand=True: 忽略 Excel 里的冻结窗格和隐藏行列, 全部展开。

    用于页面上的「显示隐藏行列」按钮 —— 有些人就是要看被筛选/手动隐藏的内容。
    """
    z = zipfile.ZipFile(io.BytesIO(data))
    theme = _theme_palette(z)
    styles = _load_styles(z, theme)

    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        shared = [_text_of(si) for si in root.findall(TAG + "si")]

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
        rels[r.get("Id")] = r.get("Target") or ""
    sheets = []
    for sh in wb.find(TAG + "sheets"):
        target = rels.get(sh.get("{%s}id" % NS_R), "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = "xl/" + target
        state = (sh.get("state") or "").strip().lower()
        sheets.append((sh.get("name") or "Sheet", path, state))

    # 隐藏工作表(模板变更履历那类)不该出现在标签栏里，更不该默认展示 ——
    # Excel 打开时看到的是 workbookView 的 activeTab，不是第一个工作表。
    visible = [(n, p) for (n, p, st) in sheets if st not in ("hidden", "veryhidden")]
    if expand:
        # 「显示隐藏行列」模式：连隐藏工作表也一并列出来（Excel 里要"取消隐藏"才看得到）
        visible = [(n, p) for (n, p, _st) in sheets]
    if not visible:
        visible = [(n, p) for (n, p, _st) in sheets]
    default_idx = 0
    view = wb.find("%sbookViews/%sworkbookView" % (TAG, TAG))
    try:
        act = int(view.get("activeTab") or 0) if view is not None else 0
    except (TypeError, ValueError):
        act = 0
    vis_names = [v[0] for v in visible]
    for i in range(max(0, act), len(sheets)):
        if sheets[i][0] in vis_names:
            default_idx = vis_names.index(sheets[i][0])
            break

    parsed = []
    for name, path in visible:
        try:
            sh = _parse_sheet(z, path, shared, styles, theme)
        except (KeyError, ET.ParseError):
            continue
        if expand:
            # 展开模式: 无视「隐藏」「冻结」, 按原始数据全量显示
            sh["hidden_rows"] = set()
            for v in sh["cols"].values():
                v["hidden"] = False
            sh["pane"] = {"x": 0, "y": 0}
        parsed.append((name, path, sh))
    if not parsed:
        raise RuntimeError("没有可解析的工作表")

    base_font = styles["fonts"][0] if styles["fonts"] else {"name": "Calibri", "size": 11}
    base_pt = float(base_font.get("size") or 11)

    css, font_cls, fill_cls, align_cls, border_cls = [], {}, {}, {}, {}

    def _cls(reg, rule, css_rule):
        name = reg.get(rule)
        if name is None:
            name = "c%d" % len(css)
            reg[rule] = name
            css.append(".%s{%s}" % (name, css_rule))
        return name

    def _font_class(font):
        if not font:
            return ""
        rule = (font.get("name"), font.get("size"), font.get("bold"),
                font.get("italic"), font.get("underline"), font.get("color"))
        if rule in font_cls:
            return font_cls[rule]
        fname = font.get("name") or base_font.get("name")
        # 符号字体不能写进 CSS：装了 Wingdings 的机器会把 ASCII 渲染成一堆图标，
        # 没装的机器又会落到随机字体（用户看到的"乱码"）。字符已经在上游换成
        # Unicode（☐ ☑ ✓ 等），这里统一用普通字体渲染，任何机器结果一致。
        if symbol_fonts.is_symbol_font(fname):
            fname = "微软雅黑"
        parts = ["font-family:'%s'" % fname]
        parts.append("font-size:%.1fpx" % (float(font.get("size") or base_pt) * 96 / 72))
        if font.get("bold"):
            parts.append("font-weight:700")
        if font.get("italic"):
            parts.append("font-style:italic")
        if font.get("underline"):
            parts.append("text-decoration:underline")
        if font.get("color"):
            parts.append("color:%s" % font["color"])
        name = "f%d" % len(font_cls)
        font_cls[rule] = name
        css.append(".%s{%s}" % (name, ";".join(parts)))
        return name

    def _fill_class(color):
        if not color:
            return ""
        return _cls(fill_cls, color, "background:%s" % color)

    def _align_class(h, v, wrap):
        key = (h, v, wrap)
        if key in align_cls:
            return align_cls[key]
        parts = []
        if h in ("center", "right", "left", "justify"):
            parts.append("text-align:%s" % ("left" if h == "justify" else h))
        if v in ("center", "top", "bottom"):
            parts.append("vertical-align:%s" % {"center": "middle"}.get(v, v))
        parts.append("white-space:%s" % ("pre-wrap" if wrap else "pre"))
        name = "a%d" % len(align_cls)
        align_cls[key] = name
        css.append(".%s{%s}" % (name, ";".join(parts)))
        return name

    def _border_class(sides):
        if not sides:
            return ""
        key = tuple(sorted(sides.items()))
        if key in border_cls:
            return border_cls[key]
        parts = ["border-%s:%s" % (side, val) for side, val in sides.items()]
        name = "b%d" % len(border_cls)
        border_cls[key] = name
        css.append(".%s{%s}" % (name, ";".join(parts)))
        return name

    tabs, panes = [], []
    note_parts = []
    gap_segments = 0
    gap_ranges = []
    for si, (sheet_name, sheet_path, sh) in enumerate(parsed):
        rows, cols = sh["rows"], sh["cols"]
        hidden_rows = sh["hidden_rows"]
        max_r, max_c = sh["max_row"], sh["max_col"]

        # ---------------------------------------------------------- 列压缩
        # 只保留"实际有内容"的列, 连续超长空白压成一个省略列。
        # 实测「2026年技术文件清单」的「流程图、矩阵图」: dimension 报 XFD(16383 列),
        # 但 19~16382 列全空、只在最后一列有一个格子 —— 按列逐个渲染会生成
        # 166×16383 ≈ 272 万个空格子, 浏览器必然卡死。
        used_cols = set()
        for row in rows.values():
            for (c, v, sidx, _n) in row["c"]:
                # 必须是"真的有值": 很多表单把整行格式刷到了 XFD 列, 每行都留下
                # 一堆"空但带样式"的 <c>（实测《ZL-F32 质量门评审表》单表 16360 个）,
                # 按它们展开会生成 11 万个 <col>/<th>、8.9MB HTML, 浏览器 65 秒才画完。
                # 这些列交给下面的 GAP 折叠成"省略 N 列"。
                if v not in (None, "") and not cols.get(c, {}).get("hidden"):
                    used_cols.add(c)
        # 合并单元格覆盖到的列即使没值也要留: 否则合并块跨列时会错位。
        for (r1, c1, r2, c2) in sh["merges"]:
            for c in range(c1, min(c2, c1 + 400) + 1):
                if not cols.get(c, {}).get("hidden"):
                    used_cols.add(c)
        if not used_cols:
            used_cols = {c for c in range(min(max_c + 1, 30))
                         if not cols.get(c, {}).get("hidden")} or {0}

        plan = []          # [(kind, c0, c1)]: c=真实列, g=压缩掉的空白段
        prev = -1
        for c in sorted(used_cols):
            if c > max_c:
                break
            if c - prev - 1 > GAP_COL_MIN:
                plan.append(("g", prev + 1, c - 1))
            else:
                for x in range(prev + 1, c):
                    plan.append(("c", x, x))
            plan.append(("c", c, c))
            prev = c
        if prev >= 0 and max_c > prev:
            if max_c - prev <= GAP_COL_MIN:
                for x in range(prev + 1, max_c + 1):
                    if not cols.get(x, {}).get("hidden"):
                        plan.append(("c", x, x))
            else:
                plan.append(("g", prev + 1, max_c))
        if not plan:
            plan = [("c", 0, 0)]
        gap_segments += sum(1 for (k, _a, _b) in plan if k == "g")
        for (k, _a, _b) in plan:
            if k == "g":
                gap_ranges.append((_a + 1, _b + 1))

        widths = [GAP_COL_WIDTH if k == "g"
                  else _col_px(cols.get(c0, {}).get("w", sh["default_w"]), base_pt)
                  for (k, c0, c1) in plan]
        ncols = len(plan)
        # 原列号 → 渲染序号(压缩段内的列都指向同一个序号)
        idx_of = {}
        for i, (k, c0, c1) in enumerate(plan):
            if k == "c":
                idx_of[c0] = i
            else:
                for c in range(c0, min(c1, c0 + 50000) + 1):
                    idx_of[c] = i

        # 行高: 先算每行最大字号(Excel 会自动撑高); 有"自动换行"的单元格
        # 还要按文字长度估算行数, 否则内容会被压掉一半。
        heights, row_of = [], {}
        for r in range(max_r + 1):
            if r in hidden_rows:
                continue
            row = rows.get(r)
            max_pt = base_pt
            need_px = 0.0
            if row:
                for (c, v, sidx, _n) in row["c"]:
                    xf = styles["xfs"][sidx] if sidx < len(styles["xfs"]) else None
                    if not xf or not xf["font"]:
                        continue
                    pt = float(xf["font"].get("size") or base_pt)
                    max_pt = max(max_pt, pt)
                    if xf["wrap"] and v and c in idx_of:
                        fs = pt * 96 / 72
                        box = max(10.0, widths[idx_of[c]] - 6)
                        units = sum(1.0 if ord(ch) > 127 else 0.55 for ch in str(v))
                        lines = max(1, int((units * fs) // box) + 1)
                        need_px = max(need_px, lines * fs * 1.32 + 3)
            if row and row.get("h"):
                # 显式行高: 按 Excel 的设定来, 不自动撑开
                heights.append(_row_px(row["h"], sh["default_h"], max_pt))
            else:
                heights.append(max(_row_px(None, sh["default_h"], max_pt), int(need_px)))
            row_of[r] = len(heights) - 1

        # 条件格式: 按单元格建索引(小范围直接展开)
        cf_map = {}
        for rule in sh["cf"]:
            for (r1, c1, r2, c2) in rule["ranges"]:
                area = (r2 - r1 + 1) * (c2 - c1 + 1)
                if area <= 20000:
                    for rr in range(r1, r2 + 1):
                        for cc in range(c1, c2 + 1):
                            cf_map.setdefault((rr, cc), []).append(rule)

        # 合并: 原坐标 → 渲染坐标
        origin, covered = {}, set()
        origin_by_row = {}
        covered_rows = set()
        for (r1, c1, r2, c2) in sh["merges"]:
            if r1 not in row_of or c1 not in idx_of:
                continue
            vr1, vr2 = row_of[r1], row_of.get(r2, row_of[r1])
            vc1, vc2 = idx_of[c1], idx_of.get(c2, idx_of[c1])
            if vr2 - vr1 < 1 and vc2 - vc1 < 1:
                continue
            rs, cs = vr2 - vr1 + 1, vc2 - vc1 + 1
            origin[(vr1, vc1)] = (rs, cs)
            origin_by_row.setdefault(vr1, {})[vc1] = (rs, cs)
            for rr in range(vr1, min(vr2, vr1 + 2000) + 1):
                for cc in range(vc1, min(vc2, vc1 + 3000) + 1):
                    if (rr, cc) != (vr1, vc1):
                        covered.add((rr, cc))
                        covered_rows.add(rr)

        # 冻结窗格偏移。
        # 注意: pane 里的 xSplit/ySplit 是**原始行列序号**, 隐藏行列不占位,
        # 所以要换算成"可见行列"的数量, 否则会多冻结一大片(实测某表 xSplit=41,
        # 里面还夹着隐藏列, 直接把半个屏幕钉死)。
        freeze_rows = sum(1 for r in row_of if r < sh["pane"]["y"])
        freeze_cols = sum(1 for (k, c0, _c1) in plan
                          if k == "c" and c0 < sh["pane"]["x"])
        row_top, acc = [], HEADER_H
        for h in heights:
            row_top.append(acc)
            acc += h
        col_left, acc = [], ROWNO_W
        for w in widths:
            col_left.append(acc)
            acc += w

        # 第一列是行号(corner + rn), 必须也给它一个 <col>，否则 table-layout:fixed
        # 会把 A 列的宽度套到行号列上, 之后每个单元格都拿到"右邻列"的宽度 ——
        # 整张表错位一列(列宽差异大的模板上, 文字被挤成几行、看起来完全不像 Excel)。
        cols_html = ('<col style="width:%dpx">' % ROWNO_W
                     + "".join('<col style="width:%dpx">' % w for w in widths))
        head = ['<th class="corner"></th>']
        for i, (k, c0, c1) in enumerate(plan):
            style = ' style="left:%dpx;z-index:8"' % col_left[i] if i < freeze_cols else ""
            if k == "g":
                head.append('<th class="ch gapc" title="第 %d~%d 列为空，已折叠"%s>'
                            '<span class="gd">⋯</span><span class="gr">%d-%d</span></th>'
                            % (c0 + 1, c1 + 1, style, c0 + 1, c1 + 1))
            else:
                head.append('<th class="ch"%s>%s</th>' % (style, _idx_to_col(c0)))

        body = []
        body_tds = 0
        truncated_at = None
        for r in range(max_r + 1):
            if r not in row_of:
                continue
            if body_tds > BODY_CELL_BUDGET:
                truncated_at = r
                break
            vr = row_of[r]
            row = rows.get(r)
            cells = {}
            if row:
                for (c, v, sidx, raw) in row["c"]:
                    ii = idx_of.get(c)
                    if ii is not None:
                        cells[ii] = (c, v, sidx, raw)

            # 本行要输出的渲染列 = 有单元格的 + 合并起点。
            # 其余整段空列用 colspan 一次带过 —— 这是把 272 万个空格子压到几千的关键。
            want = set(cells)
            for cc in origin_by_row.get(vr, ()):
                want.add(cc)
            want = {i for i in want if (vr, i) not in covered and i < ncols}

            tds = ['<th class="rn"%s>%d</th>'
                   % (' style="top:%dpx;z-index:8"' % row_top[vr] if vr < freeze_rows else "",
                      r + 1)]

            def _emit(i):
                item = cells.get(i)
                if item is None:
                    val, sidx, raw, c = "", 0, None, None
                else:
                    c, val, sidx, raw = item
                xf = styles["xfs"][sidx] if sidx < len(styles["xfs"]) else None
                classes = []
                if xf:
                    classes.append(_font_class(xf["font"]))
                    classes.append(_fill_class(xf["fill"]))
                    classes.append(_align_class(xf["h"], xf["v"], xf["wrap"]))
                    classes.append(_border_class(xf["border"]))
                span = ""
                if (vr, i) in origin:
                    rs, cs = origin[(vr, i)]
                    if rs > 1:
                        span += ' rowspan="%d"' % min(rs, 10000)
                    if cs > 1:
                        span += ' colspan="%d"' % cs
                style = ""
                if vr < freeze_rows:
                    style += "position:sticky;top:%dpx;z-index:3;" % row_top[vr]
                if i < freeze_cols:
                    style += "position:sticky;left:%dpx;z-index:2;" % col_left[i]
                hit = None
                if c is not None:
                    for rule in cf_map.get((r, c), ()):
                        if rule["dxf"] and _cf_match(rule, str(val) if val is not None else "", raw):
                            hit = rule["dxf"]
                            break
                if hit:
                    extra = []
                    if hit["fill"]:
                        extra.append("background:%s" % hit["fill"])
                    if hit["color"]:
                        extra.append("color:%s" % hit["color"])
                    if hit["bold"]:
                        extra.append("font-weight:700")
                    style += ";".join(extra) + ";"
                text = html.escape(str(val)) if val not in (None, "") else ""
                bg = (hit["fill"] if hit and hit.get("fill")
                      else (xf["fill"] if xf else ""))
                fg = (hit["color"] if hit and hit.get("color")
                      else ((xf["font"] or {}).get("color") if xf and xf["font"] else ""))
                if text and bg and abs(_luma(bg) - _luma(fg or "#000000")) < 40:
                    style += "color:%s;" % _contrast(bg)
                title = (' title="%s"' % html.escape(str(val))) if len(str(val or "")) > 30 else ""
                return ('<td%s%s%s>%s</td>'
                        % (' class="%s"' % " ".join(x for x in classes if x) if any(classes) else "",
                           span, title if not style else title + ' style="%s"' % style, text))

            if not want:
                if vr in covered_rows:
                    # 罕见: 整行没数据但被上方合并块跨入, 逐列补空以对齐列数
                    for i in range(ncols):
                        if (vr, i) not in covered:
                            tds.append('<td class="gapc"></td>')
                else:
                    # 整行空白(没数据、也不是合并延续行): 一个格子铺满整行
                    tds.append('<td class="gapc" colspan="%d"></td>' % ncols)
            else:
                last = -1
                for i in sorted(want):
                    gap = i - last - 1
                    if gap > 0:
                        tds.append('<td class="gapc" colspan="%d"></td>' % gap)
                    tds.append(_emit(i))
                    cs = origin.get((vr, i), (1, 1))[1] if (vr, i) in origin else 1
                    last = i + max(0, cs - 1)
                if last < ncols - 1:
                    rest = ncols - 1 - last
                    if vr in covered_rows:
                        rest -= sum(1 for i in range(last + 1, ncols)
                                    if (vr, i) in covered)
                    if rest > 0:
                        tds.append('<td class="gapc" colspan="%d"></td>' % rest)
            body.append('<tr style="height:%dpx">%s</tr>' % (heights[vr], "".join(tds)))
            body_tds += len(tds)

        # 浮动图片
        overlays = []
        if key:
            try:
                anchors = _parse_drawings(z, sheet_path)
            except Exception:
                anchors = []
            for a in anchors:
                if a["c1"] not in idx_of or a["r1"] not in row_of:
                    continue
                x = col_left[idx_of[a["c1"]]] + a["ox"]
                y = row_top[row_of[a["r1"]]] + a["oy"]
                if a.get("c2") is not None:
                    if a["c2"] not in idx_of or a["r2"] not in row_of:
                        continue
                    w = (col_left[idx_of[a["c2"]]] + a.get("ox2", 0)) - x
                    h = (row_top[row_of[a["r2"]]] + a.get("oy2", 0)) - y
                else:
                    w, h = a.get("w", 0), a.get("h", 0)
                if w < 8 or h < 8:
                    continue
                overlays.append('<img class="xlimg" src="/xlsmedia/%s/%s" '
                                'style="left:%dpx;top:%dpx;width:%dpx;height:%dpx" '
                                'loading="lazy" alt="" onerror="this.style.display=\'none\'">'
                                % (key, html.escape(a["name"]), x, y, w, h))

        tabs.append('<button class="tab%s" data-i="%d">%s</button>'
                    % (" on" if si == default_idx else "", si, html.escape(sheet_name)))
        trunc_note = ""
        if truncated_at is not None:
            trunc_note = ('<div class="trunc">该工作表数据量过大，为保证浏览流畅，'
                          '仅显示前 %d 行（共 %d 行）。完整内容请下载原文件查看。</div>'
                          % (truncated_at, max_r + 1))
        pane_html = ('<div class="pane%s" data-i="%d"><div class="canvas" style="width:%dpx">'
                     '<table><colgroup>%s</colgroup><thead><tr>%s</tr></thead>'
                     '<tbody>%s</tbody></table>%s</div>%s</div>'
                     % ("" if si == default_idx else " hide", si, ROWNO_W + sum(widths),
                        cols_html, "".join(head), "".join(body), "".join(overlays),
                        trunc_note))
        # 非首屏工作表放进 <script type="text/template">: 浏览器只当文本, 不建 DOM。
        # 实测 19 个工作表的文件, 全量建 DOM 要 7.8 万个节点, 切 tab 才插入对应的一个,
        # 首屏节点数降到 1/10, 打开时不再卡。
        if si == default_idx:
            panes.append(pane_html)
        else:
            panes.append('<script type="text/template" class="ptpl" data-i="%d">%s</script>'
                         % (si, pane_html))

    # 隐藏行列是 Excel 里本来就隐藏的(筛选/手动隐藏), 这里只是照原样显示;
    # 但用户容易误以为"数据少了", 所以在标题栏明确标注。
    hid_rows = sum(len(sh["hidden_rows"]) for _, _, sh in parsed)
    hid_cols = sum(sum(1 for v in sh["cols"].values() if v.get("hidden")) for _, _, sh in parsed)
    bits = []
    if hid_rows or hid_cols:
        bits.append("已按 Excel 原样隐藏 %d 行 / %d 列" % (hid_rows, hid_cols))
    if gap_segments:
        shown = "、".join("%d~%d" % r for r in gap_ranges[:3])
        more = " 等 %d 段" % len(gap_ranges) if len(gap_ranges) > 3 else ""
        bits.append("已折叠 %d 段空白列（第 %s%s 列，表头 ⋯ 可悬停）"
                    % (gap_segments, shown, more))
    note = "　·　".join(bits)

    title = html.escape(file_name or "表格")
    toggle = ('<a class="tbtn" href="javascript:void(0)" onclick="toggleExpand()" '
              'title="%s">%s</a>'
              % ("恢复 Excel 原样的隐藏行列与冻结窗格" if expand
                 else "显示被隐藏的行列，并取消冻结窗格",
                 "按 Excel 原样" if expand else "显示隐藏行列"))
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%%;background:#fff;color:#000;
 font-family:'%(basefont)s','Microsoft YaHei',Arial,sans-serif;font-size:%(basepx).1fpx}
#bar{height:34px;display:flex;align-items:center;gap:10px;padding:0 12px;
 background:#f7f8fa;border-bottom:1px solid #e3e6ea;font-size:12.5px;color:#3a3f47;font-weight:600}
#bar .note{font-weight:400;color:#8a9099;font-size:11.5px;margin-left:auto}
#grid{position:absolute;top:34px;left:0;right:0;bottom:32px;overflow:auto;background:#fff}
.pane.hide{display:none}
.canvas{position:relative}
table{border-collapse:collapse;table-layout:fixed;background:#fff}
th,td{border:1px solid #d9d9d9;padding:0 2px;overflow:hidden;vertical-align:bottom;
 line-height:1.25;font-weight:400;text-align:left;background:#fff}
td.gapc{border:0;border-bottom:1px solid #f0f0f0;padding:0;background:#fff}
th.gapc{padding:0;line-height:1;background:#f7f7f7}
th.gapc .gd{display:block;font-size:10px;line-height:10px;color:#c0c4c8}
th.gapc .gr{display:block;font-size:7.5px;line-height:9px;color:#aeb3b8;letter-spacing:-.3px}
th.gapc:hover{background:#eef2ff}
.trunc{margin:10px 12px;padding:10px 12px;background:#fffbe6;border:1px solid #ffe58f;
 border-radius:6px;color:#a05a00;font-size:12.5px}
#bar .ttl{flex:0 1 auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
 max-width:42%%}
#bar .note{font-weight:400;color:#8a9099;font-size:11.5px;margin-left:auto;
 overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
#bar .tbtn{flex:0 0 auto;margin-left:8px;padding:3px 10px;border:1px solid #cfd4da;
 border-radius:5px;background:#fff;color:#2f6feb;font-weight:400;font-size:12px;
 text-decoration:none;cursor:pointer}
#bar .tbtn:hover{border-color:#2f6feb;background:#f5f8ff}
thead th{position:sticky;top:0;z-index:6;background:#f0f0f0;color:#404040;
 font-weight:600;font-size:11px;text-align:center;height:%(hh)dpx;
 border:1px solid #d0d0d0}
th.rn{position:sticky;left:0;z-index:5;background:#f0f0f0;color:#6b6b6b;
 font-size:11px;font-weight:400;text-align:center;width:%(rnw)dpx;min-width:%(rnw)dpx;
 border:1px solid #d0d0d0}
th.corner{position:sticky;left:0;top:0;z-index:9;background:#e6e6e6;
 width:%(rnw)dpx;min-width:%(rnw)dpx}
tbody tr:hover td{background-image:linear-gradient(rgba(0,0,0,.03),rgba(0,0,0,.03))}
.xlimg{position:absolute;z-index:1;pointer-events:none}
#tabs{position:absolute;left:0;right:0;bottom:0;height:32px;display:flex;align-items:flex-end;
 gap:2px;padding:0 8px;background:#f3f3f3;border-top:1px solid #d0d0d0;
 overflow-x:auto;overflow-y:hidden;white-space:nowrap}
#tabs::-webkit-scrollbar{height:6px}
#tabs::-webkit-scrollbar-thumb{background:#c2c6ca;border-radius:3px}
#tabs::-webkit-scrollbar-track{background:transparent}
.tab{border:1px solid #d0d0d0;border-bottom:0;background:#eaeaea;color:#333;
 border-radius:4px 4px 0 0;padding:2px 12px;font-size:12px;cursor:pointer;height:25px;
 flex:0 0 auto}
.tab.on{background:#fff;font-weight:600;box-shadow:inset 0 2px 0 #2f6feb}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9999;
 background-image:url("%(wm)s");background-repeat:repeat}
%(css)s
</style></head><body>
<div id="bar"><span class="ttl">%(title)s</span><span class="note">%(note)s</span>%(toggle)s</div>
<div id="grid">%(panes)s</div>
<div id="tabs">%(tabs)s</div>
<div id="wm"></div>
<script>
/* 图片请求要带预览令牌(页面地址里的 kkt) */
var KKT = (function(){ var q = location.search || ''; return q.indexOf('kkt=') >= 0 ? q : ''; })();
function fixImgs(root){
  if (!KKT || !root || !root.querySelectorAll) return;
  root.querySelectorAll('img.xlimg').forEach(function(im){
    var s = im.getAttribute('src');
    if (s && s.indexOf('kkt=') < 0) im.src = s + KKT;
  });
}
function showPane(i){
  var grid = document.getElementById('grid');
  var pane = grid.querySelector('.pane[data-i="' + i + '"]');
  if (!pane) {
    // 首次切到该工作表: 从模板里取 HTML 建 DOM(只建这一次, 之后复用)
    var tpl = document.querySelector('script.ptpl[data-i="' + i + '"]');
    if (!tpl) return;
    var box = document.createElement('div');
    box.innerHTML = tpl.textContent;
    pane = box.firstElementChild;
    if (!pane) return;
    grid.appendChild(pane);
    fixImgs(pane);
  }
  grid.querySelectorAll('.pane').forEach(function(x){ x.classList.add('hide'); });
  pane.classList.remove('hide');
  grid.scrollTo(0, 0);
}
document.querySelectorAll('.tab').forEach(function(b){
  b.addEventListener('click', function(){
    document.querySelectorAll('.tab').forEach(function(x){ x.classList.remove('on'); });
    b.classList.add('on');
    showPane(+b.dataset.i);
  });
});
document.querySelectorAll('.pane').forEach(fixImgs);
/* 切换「按 Excel 原样」/「全部展开」: 改 URL 的 x 参数, 由服务端重新渲染 */
function toggleExpand(){
  var q = location.search || '';
  if (/[?&]x=1(&|$)/.test(q)) {
    q = q.replace(/([?&])x=1&?/, '$1').replace(/[?&]$/, '');
    if (q === '?' || q === '') q = '';
  } else {
    q += (q.indexOf('?') >= 0 ? '&' : '?') + 'x=1';
  }
  location.search = q;
}
</script>
</body></html>""" % {"title": title, "wm": watermark_url or "", "css": "".join(css),
                     "tabs": "".join(tabs), "panes": "".join(panes), "note": note,
                     "toggle": toggle,
                     "hh": HEADER_H, "rnw": ROWNO_W,
                     "basefont": base_font.get("name") or "Calibri",
                     "basepx": base_pt * 96 / 72}
