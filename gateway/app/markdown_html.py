"""Markdown → 彩色 HTML（网关自己渲染，纯标准库）。

为什么不用 LibreOffice：
    md 交给 LO 转 PDF，出来的是一份"黑白稿"——标题和正文同色、代码块没有底色、
    表格没有斑马纹、链接不可点。用户要的是能在浏览器里读的彩色文档。
    另外 LO 转一份 29KB 的 md 要 0.5 秒，这里用纯标准库解析只要几十毫秒。

实现范围（GFM 常用子集）：
    标题（#、setext）、段落、粗体/斜体/删除线、行内代码、
    围栏代码块（带语法高亮）、缩进代码块、有序/无序列表（可嵌套、可带任务框）、
    引用、分隔线、表格（含对齐）、链接/图片、自动链接。
"""
import html
import re

# ---------------------------------------------------------------- 语法高亮

_KEYWORDS = {
    "python": "def class return if elif else for while import from as with try "
              "except finally raise lambda yield in not and or is None True False "
              "pass break continue global nonlocal assert del async await self",
    "js": "function const let var return if else for while switch case break "
          "continue new class extends import export from default try catch finally "
          "throw typeof instanceof async await this null undefined true false",
    "java": "public private protected class interface extends implements static "
            "final void int long double float boolean char String new return if "
            "else for while switch case break continue try catch finally throw "
            "import package this super null true false void",
    "sql": "select from where group by order having insert update delete create "
           "table alter drop index join left right inner outer on as and or not "
           "null is in like limit offset union all distinct count sum avg max min",
    "shell": "if then else elif fi for while do done case esac function return "
             "export local cd echo exit set source sudo docker bash sh",
    "json": "true false null",
    "yaml": "true false null",
    "c": "int char float double void long short unsigned struct union enum static "
         "const return if else for while switch case break continue sizeof typedef "
         "include define NULL true false",
    "go": "package import func var const type struct interface return if else for "
          "range switch case break continue defer go chan map nil true false",
}
_ALIAS = {"py": "python", "python3": "python", "js": "js", "javascript": "js",
          "ts": "js", "typescript": "js", "java": "java", "sql": "sql",
          "sh": "shell", "bash": "shell", "shell": "shell", "zsh": "shell",
          "json": "json", "yaml": "yaml", "yml": "yaml", "xml": "xml",
          "html": "xml", "c": "c", "cpp": "c", "c++": "c", "h": "c",
          "go": "go", "golang": "go"}

# 注释 / 字符串优先，避免把注释里的关键词上色
_TOK = re.compile(
    r"(?P<comment>/\*.*?\*/|//[^\n]*|<!--.*?-->|--[^\n]*|#[^\n]*)"
    r"|(?P<string>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')"
    r"|(?P<number>\b0[xX][0-9a-fA-F]+\b|\b\d+(?:\.\d+)?\b)",
    re.S)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _hl_keywords(text: str, words) -> str:
    if not words:
        return html.escape(text)

    def repl(m):
        w = m.group(0)
        return '<span class="k">%s</span>' % w if w in words else w
    # 先按词切，安全转义非关键词部分
    out, pos = [], 0
    for m in _WORD.finditer(text):
        out.append(html.escape(text[pos:m.start()]))
        w = m.group(0)
        out.append('<span class="k">%s</span>' % w if w in words else w)
        pos = m.end()
    out.append(html.escape(text[pos:]))
    return "".join(out)


def highlight(code: str, lang: str) -> str:
    """返回带 <span> 的 HTML（已转义）。语言不认识就只做注释/字符串/数字。"""
    lang = _ALIAS.get((lang or "").lower(), (lang or "").lower())
    words = set(_KEYWORDS.get(lang, "").split()) if lang in _KEYWORDS else set()
    out, pos = [], 0
    for m in _TOK.finditer(code):
        out.append(_hl_keywords(code[pos:m.start()], words))
        cls = "c" if m.group("comment") else ("s" if m.group("string") else "n")
        out.append('<span class="%s">%s</span>' % (cls, html.escape(m.group(0))))
        pos = m.end()
    out.append(_hl_keywords(code[pos:], words))
    return "".join(out)


# ---------------------------------------------------------------- 行内

_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_IMG = re.compile(r"!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+\"([^\"]*)\")?\s*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(\s*([^)\s]+)(?:\s+\"([^\"]*)\")?\s*\)")
_AUTOLINK = re.compile(r"(?<![\"'>=])(https?://[^\s<>\"')]+)")


def inline(text: str) -> str:
    """行内元素：代码片段先切出来，避免里面的 * _ 被当成强调。"""
    parts, pos = [], 0
    for m in _CODE_SPAN.finditer(text):
        parts.append(_inline_text(text[pos:m.start()]))
        parts.append("<code>%s</code>" % html.escape(m.group(2)))
        pos = m.end()
    parts.append(_inline_text(text[pos:]))
    return "".join(parts)


def _inline_text(s: str) -> str:
    s = html.escape(s)
    s = _IMG.sub(lambda m: '<img src="%s" alt="%s"%s>'
                 % (m.group(2), m.group(1),
                    (' title="%s"' % m.group(3)) if m.group(3) else ""), s)
    s = _LINK.sub(lambda m: '<a href="%s"%s>%s</a>'
                  % (m.group(2),
                     (' title="%s"' % m.group(3)) if m.group(3) else "",
                     m.group(1)), s)
    s = _AUTOLINK.sub(lambda m: '<a href="%s">%s</a>' % (m.group(1), m.group(1)), s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s, flags=re.S)
    s = re.sub(r"__(.+?)__", r"<strong>\1</strong>", s, flags=re.S)
    s = re.sub(r"~~(.+?)~~", r"<del>\1</del>", s, flags=re.S)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s, flags=re.S)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s, flags=re.S)
    s = s.replace("  \n", "<br>\n")
    return s


# ---------------------------------------------------------------- 块级

_RE_HR = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
_RE_ATX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RE_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+#-]*)\s*$")
_RE_UL = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_RE_OL = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_RE_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
_RE_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")


def _table_align(sep: str):
    cells = [c.strip() for c in sep.strip().strip("|").split("|")]
    out = []
    for c in cells:
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        elif c.startswith(":"):
            out.append("left")
        else:
            out.append("")
    return out


def _split_row(line: str):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _render_list(lines, i, base_indent):
    """解析一段列表，返回 (html, 下一行下标)。支持缩进嵌套与任务框。"""
    ordered = bool(_RE_OL.match(lines[i]))
    tag = "ol" if ordered else "ul"
    items, i, n = [], i, len(lines)
    while i < n:
        m = (_RE_OL if ordered else _RE_UL).match(lines[i])
        if not m:
            # 空行：可能后面还有同级项
            if not lines[i].strip():
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                mm = (_RE_OL if ordered else _RE_UL).match(lines[j]) if j < n else None
                if mm and len(mm.group(1)) <= base_indent:
                    i = j
                    continue
            break
        indent = len(m.group(1))
        if indent > base_indent:
            sub, i = _render_list(lines, i, indent)
            if items:
                items[-1] += sub
            else:
                items.append(sub)
            continue
        content = m.group(3)
        # 续行（缩进比标记更深的普通行）
        i += 1
        cont = []
        while i < n:
            nxt = lines[i]
            if not nxt.strip():
                break
            mm = (_RE_OL if ordered else _RE_UL).match(nxt)
            if mm:
                break
            if len(nxt) - len(nxt.lstrip()) > indent:
                cont.append(nxt.strip())
                i += 1
            else:
                break
        if cont:
            content += " " + " ".join(cont)
        tm = _RE_TASK.match(content)
        box = ""
        if tm:
            box = ('<input type="checkbox" disabled%s> '
                   % (" checked" if tm.group(1).lower() == "x" else ""))
            content = tm.group(2)
        items.append("<li>%s%s</li>" % (box, inline(content)))
    return "<%s>%s</%s>" % (tag, "".join(items), tag), i


def render(text: str) -> str:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        m = _RE_FENCE.match(line)
        if m:
            fence, lang = m.group(1), m.group(2)
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith(fence[:3]):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append('<pre><code class="lang-%s">%s</code></pre>'
                       % (html.escape(lang or "text"),
                          highlight("\n".join(buf), lang)))
            continue

        m = _RE_ATX.match(line)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2)), lvl))
            i += 1
            continue

        # setext 标题
        if i + 1 < n and line.strip() and re.match(r"^\s{0,3}=+\s*$", lines[i + 1]):
            out.append("<h1>%s</h1>" % inline(line.strip()))
            i += 2
            continue
        if i + 1 < n and line.strip() and re.match(r"^\s{0,3}-+\s*$", lines[i + 1]) \
                and not _RE_UL.match(line):
            out.append("<h2>%s</h2>" % inline(line.strip()))
            i += 2
            continue

        if _RE_HR.match(line):
            out.append("<hr>")
            i += 1
            continue

        if _RE_QUOTE.match(line):
            buf = []
            while i < n and _RE_QUOTE.match(lines[i]):
                buf.append(_RE_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append("<blockquote>%s</blockquote>" % render("\n".join(buf)))
            continue

        if "|" in line and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
            head = _split_row(line)
            align = _table_align(lines[i + 1])
            i += 2
            body = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                body.append(_split_row(lines[i]))
                i += 1

            def cell(tag, txt, idx):
                a = align[idx] if idx < len(align) else ""
                style = ' style="text-align:%s"' % a if a else ""
                return "<%s%s>%s</%s>" % (tag, style, inline(txt), tag)
            th = "".join(cell("th", c, k) for k, c in enumerate(head))
            rows = "".join("<tr>%s</tr>" % "".join(
                cell("td", c, k) for k, c in enumerate(r + [""] * (len(head) - len(r))))
                for r in body)
            out.append('<div class="tbl"><table><thead><tr>%s</tr></thead>'
                       '<tbody>%s</tbody></table></div>' % (th, rows))
            continue

        if _RE_UL.match(line) or _RE_OL.match(line):
            indent = len((_RE_UL.match(line) or _RE_OL.match(line)).group(1))
            html_list, i = _render_list(lines, i, indent)
            out.append(html_list)
            continue

        if line.startswith("    ") or line.startswith("\t"):
            buf = []
            while i < n and (lines[i].startswith("    ") or lines[i].startswith("\t")
                             or not lines[i].strip()):
                buf.append(lines[i][4:] if lines[i].startswith("    ") else lines[i][1:])
                i += 1
            out.append("<pre><code>%s</code></pre>"
                       % html.escape("\n".join(buf).rstrip()))
            continue

        # 段落
        buf = [line.strip()]
        i += 1
        while i < n and lines[i].strip() \
                and not _RE_ATX.match(lines[i]) and not _RE_FENCE.match(lines[i]) \
                and not _RE_HR.match(lines[i]) and not _RE_QUOTE.match(lines[i]) \
                and not _RE_UL.match(lines[i]) and not _RE_OL.match(lines[i]) \
                and "|" not in lines[i]:
            buf.append(lines[i].strip())
            i += 1
        out.append("<p>%s</p>" % inline("\n".join(buf)))
    return "\n".join(out)


CSS = """
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#24292f;
 font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei","PingFang SC",Arial,sans-serif}
.hd{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.96);
 border-bottom:1px solid #e7eaee;padding:9px 18px;font-size:13px;color:#5b636e;
 backdrop-filter:saturate(180%) blur(6px)}
.hd b{color:#1f3a5f;font-weight:600}
.doc{max-width:900px;margin:0 auto;padding:22px 24px 80px}
h1,h2,h3,h4,h5,h6{line-height:1.35;margin:1.6em 0 .6em;font-weight:600;color:#12395f}
h1{font-size:26px;color:#0f4c81;border-bottom:2px solid #e3e9f2;padding-bottom:.3em}
h2{font-size:21px;color:#14539a;border-left:4px solid #4a90d9;padding-left:10px}
h3{font-size:17px;color:#1a6fb5}
h4{font-size:15px;color:#2b7cc0}
h5,h6{font-size:14px;color:#5b636e}
p{margin:.7em 0}
a{color:#0969da;text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:"JetBrains Mono",Consolas,"Courier New",monospace;font-size:.9em}
p code,li code,td code,th code{background:#f1f3f6;color:#b32d5e;
 border:1px solid #e6e9ef;border-radius:4px;padding:.1em .38em}
pre{background:#1f2430;color:#e6edf3;border-radius:8px;padding:14px 16px;
 overflow:auto;margin:1em 0;box-shadow:inset 0 0 0 1px rgba(255,255,255,.04)}
pre code{background:none;border:0;color:inherit;font-size:13px;line-height:1.6;
 white-space:pre}
pre .k{color:#ff7b72}      /* 关键字 */
pre .s{color:#a5d6ff}      /* 字符串 */
pre .c{color:#8b949e;font-style:italic}  /* 注释 */
pre .n{color:#79c0ff}      /* 数字 */
blockquote{margin:1em 0;padding:.6em 14px;border-left:4px solid #c8d3e0;
 background:#f7f9fc;color:#4c5661;border-radius:0 6px 6px 0}
blockquote p{margin:.3em 0}
ul,ol{padding-left:1.6em;margin:.7em 0}
li{margin:.28em 0}
li>ul,li>ol{margin:.2em 0}
hr{border:0;border-top:1px solid #e7eaee;margin:1.8em 0}
.tbl{overflow:auto;margin:1em 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{border:1px solid #e0e5ec;padding:7px 11px;text-align:left;vertical-align:top}
th{background:#f2f6fb;color:#12395f;font-weight:600;white-space:nowrap}
tbody tr:nth-child(even){background:#fafcfe}
img{max-width:100%;border-radius:4px}
del{color:#8a9099}
input[type=checkbox]{margin-right:.4em;vertical-align:-1px}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("{WM}");background-repeat:repeat}
@media print{ .hd{display:none} .doc{max-width:none;padding:0} pre{white-space:pre-wrap} }
"""


def build(text: str, file_name: str, wm_url: str) -> str:
    title = html.escape(file_name or "文档")
    body = render(text)
    # CSS 里有 100% / rgba 这类百分号，不能用 %-格式化，统一用占位符替换
    css = CSS.replace("{WM}", wm_url)
    return ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>" + title + "</title><style>" + css + "</style></head><body>"
            "<div class=\"hd\"><b>" + title + "</b> · Markdown 预览（彩色）</div>"
            "<div class=\"doc\">" + body + "</div><div id=\"wm\"></div>"
            "</body></html>")
