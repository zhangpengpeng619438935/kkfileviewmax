"""符号字体（Wingdings / Symbol）→ Unicode 映射。

为什么需要：
    Excel 模板里的勾选框一般用 Wingdings 字符（U+00A8 ☐、U+00FE ☑ 等）。
    这些码位在普通字体里是 ¨ þ ý 之类的西文符号，浏览器又没有 Wingdings，
    直接输出就是用户说的"乱码"（¨技术部门 ÿ…）。

映射来源：
    用真实的 wingding.ttf 渲染 GB2312 之外的三个码位逐一对照确认
    （见 tools/wd_grid.png / wd_probe.png），只收录能确认含义的常用字形，
    **不做猜测**：表里没有的码位保持原样，避免把剪刀映射成方框这种错位。
"""

# Wingdings: 码位 → Unicode
WINGDINGS = {
    0x95: "\u2022",   # •
    0xA0: "\u2022",   # • 实心圆点
    0x6C: "\u25CF",   # ●
    0x6D: "\u25CB",   # ○
    0x6E: "\u25A0",   # ■
    0x6F: "\u25A1",   # □
    0x71: "\u25A1",   # □
    0x72: "\u25A1",   # □
    0xA1: "\u25CB",   # ○
    0xA2: "\u25CF",   # ●
    0xA3: "\u25C9",   # ◉
    0xA4: "\u25CE",   # ◎
    0xA5: "\u25C9",   # ◉
    0xA6: "\u25EF",   # ◯
    0xA7: "\u25A0",   # ■
    0xA8: "\u2610",   # ☐ 空框（勾选框）—— 已逐个渲染确认
    0xA9: "\u25B2",   # ▲
    0xAA: "\u2726",   # ✦
    0xAB: "\u2605",   # ★
    0xAC: "\u2600",   # ☀
    0xD8: "\u27A4",   # ➤
    0xE0: "\u2192",   # →
    0xE1: "\u2191",   # ↑
    0xE2: "\u2193",   # ↓
    0xE3: "\u2197",   # ↗
    0xE4: "\u2198",   # ↘
    0xE5: "\u2190",   # ←
    0xF7: "\u25A1",   # □
    0xF8: "\u25A1",   # □
    0xF9: "\u25A1",   # □
    0xFA: "\u2717",   # ✗
    0xFB: "\u2717",   # ✗ 叉
    0xFC: "\u2713",   # ✓ 勾
    0xFD: "\u2612",   # ☒ 框中叉
    0xFE: "\u2611",   # ☑ 框中勾（最常用）
}

# Symbol 字体：小写字母是希腊字母，其余按 Symbol 的标准映射补几个常用符号
SYMBOL = {
    0x61: "\u03B1", 0x62: "\u03B2", 0x63: "\u03C7", 0x64: "\u03B4",
    0x65: "\u03B5", 0x66: "\u03C6", 0x67: "\u03B3", 0x68: "\u03B7",
    0x69: "\u03B9", 0x6A: "\u03D5", 0x6B: "\u03BA", 0x6C: "\u03BB",
    0x6D: "\u03BC", 0x6E: "\u03BD", 0x6F: "\u03BF", 0x70: "\u03C0",
    0x71: "\u03B8", 0x72: "\u03C1", 0x73: "\u03C3", 0x74: "\u03C4",
    0x75: "\u03C5", 0x76: "\u03D6", 0x77: "\u03C9", 0x78: "\u03BE",
    0x79: "\u03C8", 0x7A: "\u03B6",
    0xA3: "\u2264", 0xB3: "\u2265", 0xB9: "\u2260", 0xBB: "\u2248",
    0xB1: "\u00B1", 0xB4: "\u00D7", 0xB8: "\u00F7", 0xA5: "\u221E",
    0xAE: "\u2192", 0xAC: "\u2190", 0xAD: "\u2191", 0xAF: "\u2193",
    0xD6: "\u221A", 0xE5: "\u2211", 0xF2: "\u222B", 0xB0: "\u00B0",
}

# 按字体名挑表（字体名大小写、空格、版本后缀都可能不同，统一小写去空格比对）
_TABLES = {
    "wingdings": WINGDINGS,
    "wingding": WINGDINGS,
    "symbol": SYMBOL,
}

# 这些字体名一律视为"符号字体"：即使某个码位没有映射，也不要当成普通文字排版
SYMBOL_FONT_PREFIX = ("wingding", "webding", "marlett", "symbol", "mt extra",
                      "monotype sorts")


def is_symbol_font(name: str) -> bool:
    n = (name or "").strip().lower().replace(" ", "")
    return any(n.startswith(p.replace(" ", "")) for p in SYMBOL_FONT_PREFIX)


def _table_for(name: str):
    n = (name or "").strip().lower().replace(" ", "")
    for key, table in _TABLES.items():
        if n.startswith(key):
            return table
    return None


def translate(text: str, font_name: str) -> str:
    """把符号字体里的字符换成 Unicode；没有映射的字符原样保留。

    **只转换非 ASCII 码位**：WPS/Excel 模板经常把整格字体标成 Wingdings，
    而文字仍是正常中文/英文；如果把 ASCII 也按 Wingdings 映射（n→■、o→○），
    "Technical Department" 会变成 "Tech■ica● Depa□t○e■t"。勾选框用的是
    U+00A8 / U+00FE / U+F0FE 这类高位码位，只换这些就够了。
    """
    if not text or not is_symbol_font(font_name):
        return text
    table = _table_for(font_name)
    if not table:
        return text
    out = []
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:                       # ASCII 当普通文字，不映射
            out.append(ch)
            continue
        if 0xF000 <= cp <= 0xF0FF:          # 符号字体常用的 PUA 表示法
            cp -= 0xF000
        out.append(table.get(cp, ch))
    return "".join(out)


def has_symbol_font(name: str) -> bool:
    return is_symbol_font(name)
