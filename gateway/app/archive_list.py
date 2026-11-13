"""压缩包清单页(网关自己渲染, 不经过引擎)。

为什么自己做：
    kkFileView 的压缩包预览要先把整个包交给它的转换服务，再吐一个页面，
    实测 300~3000ms，而且包越大越慢。而"列出压缩包里的文件"这件事
    Python 标准库 zipfile/tarfile 只要几十毫秒（只读中央目录，不解压）。
    这样 zip 的首次响应从秒级降到 0.1 秒级。

2026-10-23 起清单里的文件名可以点开预览（`/archive/<fk>/view`）：
    能直接渲染的（图片/PDF/文本/Office/音视频）在网关侧出图或出页；
    读不了的（CAD/3D/可执行）在预览页给下载入口。条目字节由
    archive_browse 统一取（zip/tar 用标准库，rar/7z 用 7-Zip 静态二进制）。
"""
import html
import time

MAX_ENTRIES = 5000          # 超过就截断，避免几万个文件的包把页面撑爆


def _fmt_size(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "-"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "-"


def _fmt_time(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return "-"


_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#222;
 font-family:-apple-system,"Microsoft YaHei","PingFang SC",Arial,sans-serif;font-size:13px}
.hd{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid #eef0f3;
 padding:10px 16px 8px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.hd h1{margin:0;font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;
 text-overflow:ellipsis;max-width:52%%}
.hd .sum{color:#8a9099;font-size:12px}
.hd .q{margin-left:auto;display:flex;align-items:center;gap:8px}
.hd input{width:200px;padding:4px 8px;border:1px solid #dfe3e8;border-radius:6px;
 font-size:12px;outline:none}
.hd input:focus{border-color:#4a7cf7}
.hd .cnt{color:#8a9099;font-size:12px;min-width:64px;text-align:right}
.hd a.back{color:#2f6feb;text-decoration:none;font-size:12px;white-space:nowrap}
.hd a.back:hover{text-decoration:underline}
table{width:100%%;border-collapse:collapse}
th,td{padding:6px 12px;border-bottom:1px solid #f2f4f6;text-align:left;white-space:nowrap}
th{background:#fafbfc;color:#5b636e;font-weight:500;font-size:12px;position:sticky;top:49px}
td.name{white-space:normal;word-break:break-all}
td.num{text-align:right;color:#5b636e;font-variant-numeric:tabular-nums}
tr.dir td{color:#9aa3ad;font-weight:400}
tr:hover td{background:#fafcff}
a.f{color:#2f6feb;text-decoration:none}
a.f:hover{text-decoration:underline}
.ico{display:inline-block;width:16px;text-align:center;color:#b9c0c9;margin-right:4px}
.tip{padding:10px 16px;color:#8a9099;font-size:12px}
.tip b{color:#5b636e;font-weight:500}
.empty{padding:28px 16px;color:#8a9099;text-align:center;display:none}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div class="hd">%(back)s<h1>%(title)s</h1><span class="sum">%(summary)s</span>
  <div class="q"><input id="q" type="search" placeholder="按文件名过滤…" autocomplete="off">
  <span class="cnt" id="cnt"></span></div></div>
<table><thead><tr><th>名称</th><th style="text-align:right">原始大小</th>
<th style="text-align:right">压缩后</th><th>修改时间</th></tr></thead>
<tbody id="tb">%(rows)s</tbody></table>
<div class="empty" id="empty">没有匹配的文件</div>
<div class="tip">点文件名即可在线预览；预览页里可下载原文件。</div>
<div id="wm"></div>
<script>
(function(){
  var kkt = new URLSearchParams(location.search).get('kkt') || '';
  var chain = new URLSearchParams(location.search).get('chain') || '';
  var fk = %(fk_js)s;
  var back = document.querySelector('a.back');
  if (back) back.href = '/archive/' + fk + '/view?chain=' + back.getAttribute('data-chain')
                      + (kkt ? '&kkt=' + kkt : '');
  var rows = [].slice.call(document.querySelectorAll('#tb tr[data-p]'));
  rows.forEach(function(tr){
    var a = tr.querySelector('a.f');
    if (!a) return;
    var p = a.getAttribute('data-p');
    a.href = '/archive/' + fk + '/view?p=' + encodeURIComponent(p)
           + (chain ? '&chain=' + chain : '') + (kkt ? '&kkt=' + kkt : '');
  });
  var q = document.getElementById('q'), cnt = document.getElementById('cnt'),
      empty = document.getElementById('empty');
  function apply(){
    var v = (q.value || '').trim().toLowerCase(), n = 0, all = 0;
    [].slice.call(document.querySelectorAll('#tb tr')).forEach(function(tr){
      if (tr.classList.contains('dir')) { tr.style.display = ''; return; }
      all++;
      var hit = !v || (tr.getAttribute('data-name') || '').toLowerCase().indexOf(v) >= 0;
      tr.style.display = hit ? '' : 'none';
      if (hit) n++;
    });
    cnt.textContent = v ? (n + ' / ' + all) : '';
    empty.style.display = (v && n === 0) ? 'block' : 'none';
  }
  q.addEventListener('input', apply);
})();
</script>
</body></html>"""


def _zip_name(it) -> str:
    """还原 zip 里的文件名。

    Windows 自带压缩、老版 WinRAR/7-Zip 打包时**不设 UTF-8 标志位**（bit 11），
    文件名按本机代码页写入（中文系统是 GBK）。zipfile 一律按 cp437 解，
    于是中文变成"╗·╞≈╩╙╛⌡╣ñ│╠…"这样的乱码。
    这里把 cp437 的结果编回原始字节，再按中文字符集解一次。
    """
    name = it.filename
    if it.flag_bits & 0x800:          # 声明了 UTF-8，别再动
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:        # 已经不是 cp437 解出来的，保持原样
        return name
    for enc in ("gbk", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _tar_name(m) -> str:
    """tar 里的名字同理：tarfile 按 utf-8 + surrogateescape 解，GBK 名会留下代理字符。"""
    name = m.name
    if not any(0xDC80 <= ord(c) <= 0xDCFF for c in name):
        return name
    try:
        return name.encode("utf-8", "surrogateescape").decode("gbk")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return name


def _icon(name: str) -> str:
    ext = (name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else "")
    if ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "ico", "avif"):
        return "🖼"
    if ext == "pdf":
        return "📕"
    if ext in ("doc", "docx", "rtf", "wps", "odt"):
        return "📄"
    if ext in ("xls", "xlsx", "csv", "et", "ods", "xlsm"):
        return "📊"
    if ext in ("ppt", "pptx", "pps", "ppsx", "dps", "odp"):
        return "📽"
    if ext in ("zip", "rar", "7z", "tar", "gz", "tgz", "jar", "war"):
        return "🗜"
    if ext in ("dwg", "dxf", "step", "stp", "igs", "iges", "sldprt", "sldasm"):
        return "📐"
    if ext in ("mp4", "avi", "mov", "mkv", "mp3", "wav"):
        return "🎬"
    return "📃"


def build(entries, file_name: str, wm_url: str, file_key: str = "",
          summary: str = "", back_chain: str = "") -> str:
    """把条目列表渲染成清单页。

    entries: archive_browse.Entry 列表（name/size/packed/mtime/is_dir）
    """
    rows = []
    total_raw = total_zip = dirs = shown = 0
    # 目录先、文件后，同类按名字排序（与资源管理器一致）
    ordered = sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))
    for e in ordered:
        if shown >= MAX_ENTRIES:
            break
        shown += 1
        if e.is_dir:
            dirs += 1
            rows.append('<tr class="dir"><td class="name"><span class="ico">📁</span>%s</td>'
                        '<td class="num">-</td><td class="num">-</td><td>%s</td></tr>'
                        % (html.escape(e.name), _fmt_time(e.mtime)))
            continue
        total_raw += e.size or 0
        total_zip += e.packed or 0
        rows.append(
            '<tr data-p="%s" data-name="%s"><td class="name">'
            '<span class="ico">%s</span>'
            '<a class="f" data-p="%s" href="#">%s</a></td>'
            '<td class="num">%s</td><td class="num">%s</td><td>%s</td></tr>'
            % (html.escape(e.name, quote=True), html.escape(e.name, quote=True),
               _icon(e.name), html.escape(e.name, quote=True), html.escape(e.name),
               _fmt_size(e.size), _fmt_size(e.packed), _fmt_time(e.mtime)))

    files = shown - dirs
    if not summary:
        summary = ("%d 个条目（%d 个目录 / %d 个文件），未压缩共 %s"
                   % (len(entries), dirs, files, _fmt_size(total_raw)))
        more = len(entries) - shown
        if more > 0:
            summary += "，仅显示前 %d 个" % shown

    return _PAGE % {
        "title": html.escape(file_name or "压缩包"),
        "rows": "".join(rows) or '<tr><td colspan="4" class="tip">空压缩包</td></tr>',
        "summary": html.escape(summary),
        "wm": wm_url,
        "fk_js": '"%s"' % (file_key or "").replace('"', ""),
        "back": ('<a class="back" data-chain="%s" href="#">&#8592; 返回上级</a>'
                 % html.escape(back_chain, quote=True)) if back_chain else "",
    }
