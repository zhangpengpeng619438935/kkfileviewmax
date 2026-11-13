import asyncio
import base64
import hashlib
import io
import json
import logging
import os
import re
import time
import urllib.parse
from email.utils import formatdate
from urllib.parse import urlparse

import httpx

from . import config, db, filekey, office_pdf

log = logging.getLogger("engine")

_sem: asyncio.Semaphore | None = None
_client: httpx.AsyncClient | None = None


def sem() -> asyncio.Semaphore:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(config.MAX_CONCURRENT_CONVERT)
    return _sem


async def http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(config.CONVERT_TIMEOUT, connect=10),
                                    limits=httpx.Limits(max_connections=200,
                                                        max_keepalive_connections=50),
                                    follow_redirects=True)
    return _client


# 后面不跟 "参数名=" 的 & 视为文件名里的字面字符(而不是参数分隔符)
_LITERAL_AMP = re.compile(r"&(?![A-Za-z_][A-Za-z0-9_]*=)")


def normalize_file_url(url: str) -> str:
    """修正调用方拼接下载链接时未转义的字符。

    典型故障: 文件名含 & (如 "11-初始过程研究（Cpk & Ppk）") 或空格,
    直接拼进 query 参数值里 → 服务端按 & 切分参数 → 签名校验失败 →
    kkFileView 报 "Server returned HTTP response code: 403"。

    裸空格编码为 %20。
    """
    url = url.strip()
    if "?" not in url:
        return url.replace(" ", "%20")
    base, query = url.split("?", 1)
    query = _LITERAL_AMP.sub("%26", query)       # 调用方漏编码的裸 &
    query = query.replace(" ", "%20")
    return base + "?" + query


def to_engine_url(url: str) -> str:
    """转换成"给引擎用"的地址。

    kkFileView 会用 galimatias 对 URL 做规范化, 把 %26 还原成 &, 导致参数被截断、
    源站签名校验失败(403)。所以这里把字面 & 的 %26 再编码一层成 %2526:
    引擎解码一层后线上是 %26, 源站再解码得到字面 &, 签名才对得上。
    """
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    return base + "?" + query.replace("%26", "%2526")


def _probe_head_key(url: str) -> str:
    """最近一次探测拿到的文件头（base64，2 分钟）。给加密检测复用。"""
    return "probe:head:" + file_key_of(url)


def _content_total(resp) -> int:
    """响应的总字节数。-1 表示拿不到。

    206 的 Content-Length 是"这次传了多少"（80），总长只在 Content-Range 里，
    所以必须优先解析 `bytes 0-79/12345`。
    """
    cr = resp.headers.get("content-range") or ""
    if "/" in cr:
        tail = cr.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            return int(tail)
    cl = resp.headers.get("content-length") or ""
    if cl.isdigit():
        return int(cl)
    return -1



async def _get_source(client, url: str, timeout: int = 180, tries: int = 3):
    """下载源文件；连接类失败快速重试。

    业务侧给的是**短时签名 URL + 动态端口**（实测同一个 probe.docx 的地址 10 分钟内
    端口从 52157 跳到 60462、55801、65187），某个实例刚好被回收时会有一次性的
    `httpx.ConnectError: All connection attempts failed`。

    这类失败立刻重试通常就好了，不该直接判任务死刑 —— 判了会写 5 分钟的 failmsg，
    而监控每 2 秒探一次，就是几十条 422「近期转码失败」的噪音（2026-11-11 实测：
    2 次真实失败 → 32 条 422）。只重试连接/超时类，HTTP 4xx/5xx 原样抛出。
    """
    last = None
    for i in range(tries):
        try:
            return await client.get(url, timeout=timeout)
        except Exception as exc:
            last = exc
            msg = str(exc).lower()
            transient = ("connection" in msg or "connect" in msg
                         or "timed out" in msg or "timeout" in msg)
            if not transient or i >= tries - 1:
                raise
            await asyncio.sleep(0.4 * (i + 1))
    raise last

async def probe_file(url: str, timeout: int = 0):
    """预检文件地址能否下载, 返回 (ok, status_code, detail)。

    用于把"引擎下载失败"提前成接口层明确错误。注意:
    - 超时(或网络异常)时返回 (True, 0, ...), 表示"探测无结论", **不阻塞接口**,
      交给后台转码任务处理——源站响应慢时不能让用户干等。
    - 结果缓存 5 分钟, 避免同一文件反复探测慢源站。

    缓存值存 JSON（ok/status/detail 三段）。以前只存 "ok"/"bad" 两个词，
    回读时 `int(cached == "bad")` 把状态码写成 1，用户看到的是
    "文件地址不可访问(HTTP 错误(缓存))"——分不清 404 还是 403，排查得重放请求。
    """
    timeout = timeout or config.PROBE_TIMEOUT
    r = await db.get_redis()
    cache_key = "probe:" + file_key_of(url)
    # 受控文件本来就挂在 /cephfs 上，**以本地为准，不问源站**。
    # 业务接口（127.0.0.1:8080/api/file/raw）要用户会话，服务端 GET 一律 403，
    # 以前这种文件在 preview/url 就被判"文件地址不可访问"直接拒掉 ——
    # 文件明明就在手边、转换也能读本地副本，用户却完全打不开。
    # 顺手把文件头 80 字节写进缓存，_source_encryption 判断加密时还要用。
    _lp = local_path_of(url)
    # 只有 `?path=` 那类才跳过源站探测。它是业务接口（要用户会话），服务端 GET 必然 403，
    # 探测没有信息量；而 `/files/` 直链是静态服务器，探测它等于在尊重业务侧的
    # 可用性与权限判断 —— 跳过的话，业务侧明明拒绝的文件我们也能从本地副本打开。
    _rel = (urllib.parse.parse_qs(urlparse(url).query).get("path") or [""])[0]
    if _lp and _rel:
        try:
            _st = os.stat(_lp)
            with open(_lp, "rb") as _f:
                _head = _f.read(80)
            try:
                await r.set(_probe_head_key(url),
                            base64.b64encode(_head).decode(), ex=120)
            except Exception:
                pass
            log.info("本地有副本，跳过源站探测(%s): %d 字节", file_key_of(url)[:12], _st.st_size)
            return True, 200, ""
        except OSError:
            pass                        # 本地没有，继续走 HTTP
    try:
        cached = await r.get(cache_key)
        if cached and cached not in ("ok", "bad"):
            d = json.loads(cached)
            return bool(d.get("ok")), int(d.get("status") or 0), d.get("detail") or ""
        # 旧格式（部署后 5 分钟内还会读到），只能给出概括
        if cached == "bad":
            return False, 0, "HTTP 错误(缓存)"
        if cached == "ok":
            return True, 200, ""
    except Exception:
        pass
    client = await http()
    try:
        # 一次 Range 请求同时拿到三样东西：可达性、真实大小、文件头 80 字节。
        # 文件头是给 _source_encryption 判"是否被文档加密系统包裹"用的——
        # 它原来自己再发一条 80 字节请求，每个首次预览白多 100ms。
        # 用 stream + 读满 80 字节就停：源站忽略 Range 返回全量时也不会真的下载。
        head = b""
        async with client.stream(
                "GET", url, headers={"Range": "bytes=0-79"},
                timeout=timeout, follow_redirects=True) as resp:
            status = resp.status_code
            body_len = _content_total(resp)
            if status < 400:
                async for chunk in resp.aiter_bytes():
                    head += chunk
                    if len(head) >= 80:
                        break
        if body_len < 0:
            body_len = len(head)          # 拿不到总长时用已读到的字节数兜底
        if head:
            try:
                await r.set(_probe_head_key(url),
                            base64.b64encode(head[:80]).decode(), ex=120)
            except Exception:
                pass
        ok = status < 400 and body_len > 0
        detail = ""
        if status >= 400:
            detail = "HTTP %s" % status
        elif body_len <= 0:
            # 源站给了 200 但内容是空的: 不拦的话会一路走到 pdf.js 报
            # "The PDF file is empty, i.e. its size is zero bytes", 看不出是源文件的问题
            detail = "源站返回 0 字节(该文件在源站为空或已损坏)"
        elif (config.MAX_FILE_SIZE_MB > 0
              and body_len > config.MAX_FILE_SIZE_MB * 1048576):
            # 接口文档一直宣称有这个上限，实际从没实现过。这些路径会把整个文件
            # 读进内存（压缩包、Office 转换），几百 MB 的 xls/pptx 会直接把 worker 吃满。
            detail = "文件 %.0f MB，超过 %d MB 上限" % (
                body_len / 1048576.0, config.MAX_FILE_SIZE_MB)
            try:
                await r.set(cache_key, json.dumps(
                    {"ok": False, "status": 413, "detail": detail},
                    ensure_ascii=False), ex=300)
            except Exception:
                pass
            return False, 413, detail
        try:
            await r.set(cache_key, json.dumps(
                {"ok": ok, "status": status, "detail": detail},
                ensure_ascii=False), ex=300)
        except Exception:
            pass
        return ok, status, detail
    except Exception as exc:
        # 超时/不可达: 不作结论, 放行给后台任务
        try:
            await r.set(cache_key, json.dumps(
                {"ok": True, "status": 0, "detail": ""},
                ensure_ascii=False), ex=60)     # 短暂放行, 60 秒后再试
        except Exception:
            pass
        return True, 0, str(exc)[:160]


# 签名/时效类查询参数: 每次打开都会变, 不能算进"文件身份"
_VOLATILE_PARAMS = {
    "exp", "expires", "sig", "sign", "signature", "token", "access_token",
    "auth", "authorization", "timestamp", "ts", "_t", "nonce",
    "x-amz-signature", "x-amz-credential", "x-amz-date", "x-amz-expires",
    "x-amz-security-token", "awsaccesskeyid", "ossaccesskeyid",
}


def file_key_of(url: str) -> str:
    """缓存键 = 文件身份，**不含签名/有效期/一次性令牌**。

    实现在 filekey.py（纯标准库），预转脚本 import 同一份 —— 这个函数以前在
    engine.py 和 preconvert.py 各存一份副本，改一处忘另一处就会出现
    "预转按这个键落盘、用户点开按那个键查找"，预转天天跑却命中率为 0。
    口径与踩过的坑见 filekey.py 文件头。
    """
    return filekey.of(url)


def ext_of(url: str) -> str:
    path = urlparse(url.split("?")[0]).path
    if "." in path:
        return path.rsplit(".", 1)[-1].lower()
    return ""


def name_of(url: str) -> str:
    """从 URL 推断文件名(解码 %XX, 否则中文/空格名会显示成编码串)。

    `unquote()` 固定按 UTF-8 解，遇到老系统用 GBK 转义的地址（
    `%D7%D4%B6%AF...`）会变成一串 U+FFFD，标题和审计记录里全是问号。
    这里先还原成原始字节，再按 UTF-8 → GB18030 依次试。
    """
    from urllib.parse import unquote_to_bytes
    path = urlparse(url.split("?")[0]).path
    raw = path.rsplit("/", 1)[-1] if "/" in path else path
    data = unquote_to_bytes(raw)
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def file_type_of(ext: str) -> str:
    ext = (ext or "").lower()
    mapping = {
        "office": "doc docx xls xlsx ppt pptx wps et dps odt ods odp rtf wpt ett",
        "pdf": "pdf ofd",
        "image": "jpg jpeg png gif bmp webp svg tif tiff tga wmf emf heic psd eps",
        "txt": "txt md xml json java js css py php log csv yaml yml ini sql html htm",
        "cad": "dwg dxf dwf dwt plt cf2 iges step stp ifc",
        "media": "mp3 wav mp4 flv avi mov mkv webm eml msg epub xmind bpmn drawio dcm",
        "zip": "zip rar 7z tar gz jar",
    }
    for ftype, exts in mapping.items():
        if ext in exts.split():
            return ftype
    return "other"


def encode_url(url: str) -> str:
    """kkFileView 要求 Base64 + URLencode 双重编码。"""
    return urllib.parse.quote_plus(base64.b64encode(url.encode("utf-8")).decode())


def engine_preview_url(url: str) -> str:
    q = encode_url(url)
    return "%s/onlinePreview?url=%s" % (config.KK_BASE, q)


def check_source_allowed(url: str):
    """文件来源白名单与扩展名黑名单。"""
    ext = ext_of(url)
    if ext in config.BLOCK_EXTS:
        raise ValueError("禁止预览该文件类型: .%s" % ext)
    host = urlparse(url).hostname or ""
    if config.TRUST_HOSTS:
        ok = False
        for rule in config.TRUST_HOSTS:
            rule = rule.strip()
            if rule.startswith("*."):
                if host.endswith(rule[1:]) or host.endswith(rule[2:]):
                    ok = True
                    break
            elif host == rule or host.endswith("." + rule):
                ok = True
                break
        if not ok:
            raise ValueError("文件来源不在信任列表内: %s" % host)


def _rk(file_key: str) -> str:
    return "kk:ready:%s" % file_key


# ---- "Univer 快照生成不了" 的标记 ----------------------------------------
# 为什么需要它：xlsx 走 /univer 时只认 .univsnap.json。快照生成失败
# （文件残缺、openpyxl 不支持的结构、超大表超时）时接口返回 500，
# 前端拿不到数据，页面就一直是白的 —— 用户体感是"加载特别慢"，
# 而实际上静态 HTML 表格（.html）早就生成好了、就在磁盘上。
# 打上这个标记，路由就会绕过 /univer 直接给静态表格页，至少能看能滚。
# TTL 7 天：源文件换了会由 invalidate() 清掉，不会一直卡在降级态。
_NOSNAP_KEY = "kk:nosnap:%s"


async def mark_nosnap(file_key: str, err: str = "", ttl: int = 86400 * 7):
    """记住"这个文件的表格快照生成不了"，路由据此退到静态表格页。"""
    if not file_key:
        return
    try:
        r = await db.get_redis()
        await r.set(_NOSNAP_KEY % file_key, str(err or "1")[:200], ex=ttl)
    except Exception:
        pass


async def clear_nosnap(file_key: str):
    try:
        r = await db.get_redis()
        await r.delete(_NOSNAP_KEY % file_key)
    except Exception:
        pass


async def nosnap(file_key: str) -> bool:
    try:
        r = await db.get_redis()
        return bool(await r.get(_NOSNAP_KEY % file_key))
    except Exception:
        return False


async def mark_ready(file_key: str, ttl: int = 86400 * 7, mode: str = "pdf"):
    """标记就绪。mode=sheet 时还要校验渲染文件是否还在(容器重启会丢 /app/sheets)。

    注意: **不要覆盖已有的 "sheet" 标记**。表格/SVG 渲染成功后 convert_task 结尾
    还会再调一次 mark_ready(file_key)（默认 mode="pdf"），若把 "sheet" 盖成 "pdf"，
    is_ready 就不再校验渲染文件是否存在 —— 文件一丢就会 404，用户侧表现为永远"渲染中"。
    """
    r = await db.get_redis()
    key = _rk(file_key)
    if mode == "pdf":
        try:
            if await r.get(key) in ("sheet", "pdf_local", "cad"):
                return
        except Exception:
            pass
    await r.set(key, mode, ex=ttl)


async def ready_mode(file_key: str) -> str:
    """返回就绪模式: sheet(表格) / pdf_local(网关本地转的 PDF) / pdf(引擎转) / ""。"""
    try:
        r = await db.get_redis()
        v = await r.get(_rk(file_key))
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        return v or ""
    except Exception:
        return ""


async def is_ready(file_key: str) -> bool:
    r = await db.get_redis()
    v = await r.get(_rk(file_key))
    if v == "sheet":
        from . import sheet
        # 大表跳过 HTML 直接出 Univer 快照，所以两个产物任一对就算就绪
        if sheet.sheet_ready(file_key) or sheet.snap_ready(file_key):
            return True
        return False          # 渲染文件已丢失, 需重新生成
    if v == "pdf_local":
        from . import sheet
        return sheet.local_pdf_ready(file_key)   # 本地 PDF 丢了也要重转
    if v == "cad":
        from . import cad_view
        return cad_view.pdf_ready(file_key)      # CAD 的 PDF 缓存丢了要重转
    if v:
        return True
    row = await db.query_one(
        "SELECT status, file_url FROM preview_task WHERE file_key=%s AND status='ready' "
        "ORDER BY ready_at DESC LIMIT 1", (file_key,))
    if row:
        # 表格/SVG 模式: 渲染文件必须还在, 否则要重新生成(容器重启会丢 /app/sheets)
        src_url = row["file_url"] or ""
        if _is_direct_render(src_url):
            from . import sheet
            if sheet.sheet_ready(file_key):
                await mark_ready(file_key, mode="sheet")
                return True
            return False
        # 本地转 PDF 的类型(md/office): 必须确认 PDF 真在磁盘上。
        # 否则库里残留一条旧的 ready(比如早期"md 当纯文本"的任务),
        # 就会绕过转换直接跳引擎, 用户看到的又是纯文本。
        if ext_of(src_url) in config.LOCAL_CONVERT_EXTS:
            from . import sheet
            return sheet.local_pdf_ready(file_key)
        # CAD 也要盯产物：Redis 标记丢了、或 30 分钟过期把 .cad.pdf 删了，
        # 只靠任务表的 ready 就会误判"可看"，然后一路放行到引擎的 pdf.js 页面
        # ——那条路渲染复杂图纸要几分钟（页面全白），用户以为文件坏了。
        if ext_of(src_url) in config.CAD_EXTS and config.CAD_RASTER:
            from . import cad_view
            if cad_view.pdf_ready(file_key):
                await mark_ready(file_key, mode="cad")
                return True
            return False
        return True
    # 表格模式: 渲染结果已落盘即视为就绪
    if config.SHEET_PREVIEW or config.SVG_PREVIEW:
        from . import sheet
        if sheet.sheet_ready(file_key):
            await mark_ready(file_key, mode="sheet")
            return True
    return False


_ERR_MARKERS = ("暂不支持", "文件预览失败", "notSupported", "previewFailed")


def engine_page_error(text: str) -> str:
    """引擎下载/转换失败时仍返回 HTTP 200 的提示页, 这里从页面内容里识别失败原因。

    不这样判断的话, 失败会被误标记成 ready, 用户打开才看到"系统暂不支持在线预览"。
    """
    if not text or not any(m in text for m in _ERR_MARKERS):
        return ""
    m = re.search(r"Server returned HTTP response code:\s*(\d+)", text)
    if m:
        return "引擎下载文件失败, 源站返回 HTTP %s" % m.group(1)
    m = re.search(r"具体原因如下：(.*?)</p>", text, re.S)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()[:200]


def _is_direct_render(file_url: str) -> bool:
    """是否由网关自己渲染(表格 / SVG / 压缩包清单 / Markdown), 而不是交给引擎或 LibreOffice。"""
    ext = ext_of(file_url)
    if config.XLSX_HTML and ext == "xlsx":
        return True
    if config.MD_HTML and ext in config.MD_EXTS:
        return True
    if config.ARCHIVE_RENDER and ext in config.ARCHIVE_EXTS:
        return True
    return ((config.SHEET_PREVIEW and ext in config.SHEET_EXTS)
            or (config.SVG_PREVIEW and ext in config.SVG_EXTS))


async def _render_markdown(file_url: str, file_key: str, file_name: str):
    """Markdown → 彩色 HTML(标题/代码/表格/链接都有自己的颜色)。

    交给 LibreOffice 转 PDF 出来是黑白稿, 而且 29KB 的 md 要 0.5 秒;
    这里纯标准库解析, 实测 30~60ms。
    """
    from . import markdown_html, sheet
    client = await http()
    fr = await _get_source(client, file_url)
    if fr.status_code >= 400:
        raise RuntimeError("下载源文件失败 HTTP %s" % fr.status_code)
    raw = fr.content
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", "replace")
    page = markdown_html.build(text, file_name, sheet.watermark_bg_url())
    os.makedirs(sheet.SHEET_DIR, exist_ok=True)
    tmp = sheet.sheet_path(file_key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, sheet.sheet_path(file_key))


def trim_arc_cache():
    """压缩包缓存的总量保护：超出上限就按 mtime 删最旧的。

    统计的是包本体(.arc)与包内 Office 转出来的临时 PDF(.arc-*.pdf)，
    两类都不受其它产物清理逻辑覆盖，只能靠这里兜住。
    """
    from . import sheet
    limit = config.ARCHIVE_CACHE_TOTAL_MB * 1048576
    try:
        items = []
        for n in os.listdir(sheet.SHEET_DIR):
            if not (n.endswith(".arc") or (".arc-" in n and n.endswith(".pdf"))):
                continue
            p = os.path.join(sheet.SHEET_DIR, n)
            try:
                st = os.stat(p)
            except OSError:
                continue
            items.append((st.st_mtime, st.st_size, p))
    except OSError:
        return
    total = sum(i[1] for i in items)
    if total <= limit:
        return
    for _, size, p in sorted(items):
        try:
            os.remove(p)
            total -= size
        except OSError:
            continue
        if total <= limit:
            break


def arc_cache_path(file_key: str) -> str:
    """压缩包本体缓存（清单里的文件点开预览时按条目取字节用）。"""
    from . import sheet
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".arc")


async def _render_archive(file_url: str, file_key: str, file_name: str):
    """压缩包 → 可点开的文件清单页。只读目录, 不解压, 通常几十毫秒。

    引擎渲染压缩包要 300~3000ms(包越大越慢), 而这一步完全不需要引擎。
    zip/tar 用标准库；rar/7z 用挂载进来的 7-Zip 静态二进制（见 archive_browse）。

    顺手把包本体留一份（<key>.arc）：用户点开清单里的某个文件时，
    要按条目名取字节，不可能每次都重新下载几十 MB 的包。
    磁盘占用由 trim_arc_cache() 的总量上限统一淘汰。
    """
    from . import archive_browse, archive_list, sheet
    ext = ext_of(file_url)
    client = await http()
    fr = await _get_source(client, file_url)
    if fr.status_code >= 400:
        raise RuntimeError("下载源文件失败 HTTP %s" % fr.status_code)
    body = fr.content
    os.makedirs(sheet.SHEET_DIR, exist_ok=True)

    # 包本体一律落盘：点开条目时要按条目取字节，rar/7z 更是只能读磁盘文件。
    # 磁盘占用由 trim_arc_cache() 的总量上限兜住，不再按单包大小分叉。
    trim_arc_cache()
    src = arc_cache_path(file_key)
    tmp = "%s.%d.part" % (src, os.getpid())
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, src)
    entries = archive_browse.listing(src, ext)
    page = archive_list.build(entries, file_name, sheet.watermark_bg_url(), file_key)
    tmp = sheet.sheet_path(file_key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, sheet.sheet_path(file_key))


async def _build_univsnap(data: bytes, file_name: str, file_key: str,
                          timeout: float = 0) -> bool:
    """把 xlsx 解析成 Univer 快照并落盘（预热用）。

    为什么要单独做这一步：用户点开表格走的是 /univer，它只认 .univsnap.json。
    原来预热只生成 .html 轻量视图，用户打开时 Univer 还得现场用 openpyxl 解析，
    超过 SHEET_SNAPSHOT_TIMEOUT(20 秒) 就降级成 PDF —— 表现就是"表格打开是 PDF、
    格式死板"。预热时把快照做好，用户打开直接读 JSON。
    这里超时放宽到 UNIV_SNAP_PREWARM_TIMEOUT（默认 120 秒），因为没人盯着屏幕。
    """
    from . import sheet, univer_data
    snap = os.path.join(sheet.SHEET_DIR, (file_key or "") + ".univsnap.json")
    try:
        if os.path.getsize(snap) > 0:
            await clear_nosnap(file_key)      # 快照在，之前失败过也作废
            return True
    except OSError:
        pass
    cap = timeout or config.UNIV_SNAP_PREWARM_TIMEOUT
    t0 = time.time()
    try:
        loop = asyncio.get_event_loop()
        wb = await asyncio.wait_for(
            loop.run_in_executor(None, univer_data.xlsx_to_workbook, data, file_name),
            timeout=cap)
    except asyncio.TimeoutError:
        log.warning("快照生成超时(%ds)，退回 PDF: %s", cap, file_key)
        await mark_nosnap(file_key, "snapshot timeout %ds" % cap)
        return False
    except Exception as exc:
        log.warning("预热快照失败(%s): %s", file_key, exc)
        await mark_nosnap(file_key, exc)
        return False
    try:
        os.makedirs(sheet.SHEET_DIR, exist_ok=True)
        tmp = snap + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(wb, f, ensure_ascii=False)
        os.replace(tmp, snap)
    except OSError as exc:
        log.warning("快照落盘失败(%s): %s", file_key, exc)
        await mark_nosnap(file_key, "save failed: %s" % exc)
        return False
    await clear_nosnap(file_key)
    log.info("表格快照已预生成(%s): %.1fs", file_key, time.time() - t0)
    return True


async def _render_xlsx_html(file_url: str, file_key: str, file_name: str) -> bool:
    """xlsx → 原生 HTML 表格。不转 PDF, 因此不会被打印区域裁列、也不会按 A4 分页。

    返回 True 表示"表格太大，已改走 PDF"，调用方要把就绪模式记成 pdf_local。
    """
    from . import sheet, univer_data, xlsx_html
    client = await http()
    fr = await _get_source(client, file_url)
    data = fr.content if fr.status_code < 400 else None
    if data is None:
        # 业务接口（127.0.0.1:8080/api/file/raw）要用户会话，服务端 GET 只会得到 403。
        # 但受控文件本身就挂在 /cephfs 上 —— 为了一个鉴权把能看的旧产物删掉、
        # 还转不回来，是最冤的一种失败。这里退化成本地副本。
        data = local_bytes(file_url)
        if data is None:
            raise RuntimeError("下载源文件失败 HTTP %s" % fr.status_code)
        log.info("源站返回 %s，改用本地副本重转(%s): %d KB",
                 fr.status_code, file_key, len(data) // 1024)
    # 格式刷到 XFD 列的表格：openpyxl 按 dimension 建对象要 28s，
    # 原生 HTML 也会撑成 8.9MB / 11 万个 <col>（浏览器 65s 才画完）。
    # LibreOffice 转 PDF 按真实内容排版，同一份文件 3 秒 8 页，是唯一能接受的。
    why = univer_data.heavy_reason(data)
    if why:
        # 先试一把快照再决定转不转 PDF。判"大"只看 dimension，而 dimension
        # 经常被格式刷到 XFD（实测某 BOM 表 16384 列、实际只有 2 万个单元格，
        # 解析 0.4 秒）—— 这种表一转 PDF 就没法选中/筛选，纯属误伤。
        if await _build_univsnap(data, file_name, file_key, timeout=30):
            log.info("表格 dimension 虚高但快照可用，仍走 Univer(%s): %s",
                     file_key, why)
            return False
        log.info("表格过大改走 PDF(%s): %s", file_key, why)
        out = sheet.local_pdf_path(file_key)
        os.makedirs(sheet.SHEET_DIR, exist_ok=True)
        await office_pdf.to_pdf(data, ext_of(file_url), out)
        return True
    page = xlsx_html.build(data, file_name, sheet.watermark_bg_url(), file_key)
    os.makedirs(sheet.SHEET_DIR, exist_ok=True)
    # 原子写：重转时如果有人的页面正读着这份 HTML/源副本，直接 open(w) 会让它读到半截
    # （表格页是整页 HTML，截断后就是白屏）。
    tmp = sheet.sheet_path(file_key) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, sheet.sheet_path(file_key))
    # 浮动图片按需从源文件里读, 缓存一份(几十 MB 的 xlsx 也只占一份)
    tmp = src_xlsx_path(file_key) + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, src_xlsx_path(file_key))
    # 再顺手把 Univer 快照做好：用户点开表格走 /univer，只预 HTML 等于没预 ——
    # 打开时仍要现场解析，超 20 秒就掉 PDF（"表格变 PDF"就是这么来的）
    await _build_univsnap(data, file_name, file_key)


def src_xlsx_path(file_key: str) -> str:
    from . import sheet
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".src.xlsx")


_file_locks = {}


def file_lock(file_key: str):
    """进程内锁(仅作兜底)。网关是 2 个 uvicorn worker, 跨进程要靠 Redis 抢占。

    超上限时只淘汰"当前没人在等"的锁。原来的 `clear()` 会把别人正在持有的
    锁对象也扔掉，同文件的下一个协程拿到一把新锁，两把锁互不排斥 ——
    临界区形同虚设。
    """
    lk = _file_locks.get(file_key)
    if lk is None:
        if len(_file_locks) > 5000:
            for k in [k for k, v in _file_locks.items() if not v.locked()][:4000]:
                _file_locks.pop(k, None)
        lk = asyncio.Lock()
        _file_locks[file_key] = lk
    return lk


def _claim_key(file_key: str) -> str:
    return "kk:task:" + file_key


async def task_claim(file_key: str, task_id: str, ttl: int = 0) -> bool:
    """原子抢占: 同一个文件只允许一个请求去创建转码任务。

    **ttl 必须盖住"最长转换时间"**，否则长转换跑到一半锁就没了，后来的请求会
    再起一路 —— 实测 W02_HUD_-240823.pptx 要 333 秒，而这里原来写死 300 秒，
    11-11 11:03:11 和 11:03:12 就是这么叠出两条记录的（两路 LibreOffice 互相
    覆盖产物，白烧一整轮 CPU）。判活那边一直用 CONVERT_TIMEOUT+120，
    锁却只给 CONVERT_TIMEOUT，两边本来就不一致。
    """
    ttl = ttl or (config.CONVERT_TIMEOUT + 120)
    try:
        r = await db.get_redis()
        return bool(await r.set(_claim_key(file_key), task_id, nx=True, ex=ttl))
    except Exception:
        return True          # Redis 异常时退化为"允许创建", 不影响可用性


async def claimed_task(file_key: str) -> str:
    try:
        r = await db.get_redis()
        v = await r.get(_claim_key(file_key))
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        return v or ""
    except Exception:
        return ""


# 释放抢占：先比对自己的 id 再删。无条件 del 会在 A 的任务还没跑完时把 A 的锁
# 删掉（B 抢失败后走到这个分支），第三个请求立刻又能抢到 —— 同一文件叠好几路转换。
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


async def release_task(file_key: str, task_id: str = ""):
    if not task_id:
        return                      # 不知道持有者就什么都不做，让别人自己释放
    try:
        r = await db.get_redis()
        await r.eval(_RELEASE_LUA, 1, _claim_key(file_key), task_id)
    except Exception:
        pass


async def task_alive(task_id: str) -> bool:
    """任务是否还活着（有没有可能继续推进）。

    必须带时间窗：进程重启后 `queued` 行会永久留在表里，只看 status 的话
    这个文件从此被认为"已有任务在跑"，再也不转换，用户一直看到"转换中"。
    """
    if not task_id:
        return False
    try:
        row = await db.query_one(
            "SELECT status, TIMESTAMPDIFF(SECOND, updated_at, NOW()) AS idle "
            "FROM preview_task WHERE task_id=%s", (task_id,))
    except Exception:
        return False
    if not row or row["status"] not in ("queued", "converting"):
        return False
    return int(row.get("idle") or 0) < config.CONVERT_TIMEOUT + 120


async def active_task(file_key: str) -> str:
    """同一文件是否已有排队中/转换中的任务(避免并发重复起 LibreOffice)。"""
    try:
        row = await db.query_one(
            "SELECT task_id FROM preview_task WHERE file_key=%s "
            "AND status IN ('queued','converting') "
            "AND updated_at > NOW() - INTERVAL %s SECOND "
            "ORDER BY created_at DESC LIMIT 1",
            (file_key, config.CONVERT_TIMEOUT + 120))
    except Exception:
        return ""
    return row["task_id"] if row else ""


def outline_path(file_key: str) -> str:
    from . import sheet
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + "_outline.html")


# 老版二进制 Office(.ppt/.doc 等)是 OLE2 复合文档, 不是 zip;
# 而「快速内容页」和「文字降级」都只解析 OOXML 的 zip 结构。
LEGACY_PPT_EXTS = ("ppt", "pps", "pot", "dps", "odp")
LEGACY_DOC_EXTS = ("doc", "rtf", "wps", "odt", "wpt")
# 能走"文字降级"的格式(OOXML 直接解析; 老版二进制先升级成 OOXML)
TEXT_FALLBACK_EXTS = ("pptx", "ppt", "pps", "ppsx", "pptm", "ppsm", "potx",
                      "pot", "dps", "odp",
                      "docx", "doc", "rtf", "wps", "odt", "wpt")


def is_zip(data: bytes) -> bool:
    """是不是 zip 容器。

    除了标准文件头 PK\\x03\\x04，还认空归档(PK\\x05\\x06)、分卷(PK\\x07\\x08)
    与只有中央目录的包(PK\\x01\\x02)。只认 \\x03\\x04 会把"没有任何条目的空 pptx"
    当成老版二进制，报出"老版二进制格式 .pptx 无法文字降级"这种自相矛盾的错。
    """
    if not data or len(data) < 4:
        return False
    return data[:2] == b"PK" and data[2:4] in (b"\x03\x04", b"\x05\x06",
                                               b"\x07\x08", b"\x01\x02")


def not_ooxml_msg(ext: str) -> str:
    """源文件"既不是 zip 也不是可升级的老版 OOXML"时给用户看的话。

    真实场景：文件被企业文档加密系统包裹（%TSD-Header-###%）后，扩展名仍是
    .pptx，早期版本会报"老版二进制格式 .pptx 无法文字降级"——自相矛盾，
    还让人以为是服务端不支持新版格式。
    """
    ext = (ext or "").lower()
    if ext in ("docx", "pptx", "xlsx", "docm", "pptm", "xlsm", "ppsx", "potx",
               "dotx", "xltx"):
        return ("源文件不是有效的 .%s 文档：内容既不是 zip 容器，也不是可升级的"
                "老版格式，通常是被文档加密系统包裹或文件本身已损坏" % ext)
    return "该文件无法解析成可显示的内容（.%s）" % (ext or "?")


async def _upgrade_to_ooxml(data: bytes, ext: str, conv_to: str, tag: str) -> bytes:
    """把 .ppt/.doc 这类 OLE2 文件升级成 OOXML, 返回转换后的字节。"""
    from . import sheet as _sheet
    tmp = os.path.join(_sheet.SHEET_DIR, "%s.%s.tmp" % (tag, conv_to))
    try:
        cost = await office_pdf.to_ooxml(data, ext, tmp, conv_to)
        with open(tmp, "rb") as f:
            out = f.read()
        log.info("老版 .%s 已升级为 .%s(%s) 耗时 %.1fs", ext, conv_to, tag, cost)
        return out
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def src_cache_path(file_key: str) -> str:
    from . import sheet
    return os.path.join(sheet.SHEET_DIR, (file_key or "") + ".src.pptx")


async def ensure_outline(file_url: str, file_key: str, file_name: str) -> str:
    """生成(或复用)PPT 的快速内容页, 返回 HTML 文件路径。

    页面里 __KKT__ / __WM__ 是占位符, 由路由在返回时替换(令牌每次请求都不同)。
    老版 .ppt/.dps 不是 zip, 先用 LibreOffice 升级成 pptx(实测 1.2 秒) 再解析,
    这样"秒开内容页"对老格式同样成立。
    """
    path = outline_path(file_key)
    if os.path.exists(path):
        return path
    from . import office_outline, sheet
    client = await http()
    fr = await _get_source(client, file_url)
    if fr.status_code >= 400:
        raise RuntimeError("下载源文件失败 HTTP %s" % fr.status_code)
    data = fr.content
    if not is_zip(data):
        ext = ext_of(file_url)
        if ext not in LEGACY_PPT_EXTS:
            raise RuntimeError("该格式不支持快速内容页(.%s)" % (ext or "?"))
        if len(data) > config.LEGACY_OUTLINE_MAX_MB * 1024 * 1024:
            # 升级这一步是同步的, 太大的文件会让浏览器白等 → 交给等待页
            raise RuntimeError("文件 %dMB 超过内容页阈值 %dMB, 直接等待排版转换"
                               % (len(data) >> 20, config.LEGACY_OUTLINE_MAX_MB))
        data = await _upgrade_to_ooxml(data, ext, "pptx", file_key)
    page = office_outline.build_pptx(data, file_name, file_key)
    os.makedirs(sheet.SHEET_DIR, exist_ok=True)
    with open(src_cache_path(file_key), "wb") as f:     # 图片按需从这里读
        f.write(data)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(page)
    os.replace(tmp, path)
    return path


def pdf_key(file_name: str) -> str:
    """降级记忆的 key: 按**文件名**记(URL 里的签名每次都变, 按 file_key 记命中率极低)。"""
    return "kk:pdfpref:" + hashlib.sha1(
        (file_name or "").encode("utf-8")).hexdigest()[:20]


def fail_key(file_key: str) -> str:
    return "kk:failmsg:" + (file_key or "")


async def mark_failed(file_key: str, msg: str, ttl: int = 300):
    """记住某个文件近期转码失败, 避免用户重复打开时又白等一轮。"""
    if not file_key:
        return
    try:
        r = await db.get_redis()
        await r.set(fail_key(file_key), (msg or "")[:300], ex=ttl)
    except Exception:
        pass


async def recent_failure(file_key: str) -> str:
    """返回近期的失败原因(空串表示没有记录)。"""
    if not file_key:
        return ""
    try:
        r = await db.get_redis()
        v = await r.get(fail_key(file_key))
        if v is None:
            return ""
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    except Exception:
        return ""


async def pdf_preferred(file_name: str) -> bool:
    """同名文件此前是否因表格模式失败/过重而降级过 PDF(7 天内直接走 PDF)。"""
    if not file_name:
        return False
    try:
        r = await db.get_redis()
        return bool(await r.get(pdf_key(file_name)))
    except Exception:
        return False


async def mark_pdf_preferred(file_name: str):
    """记住: 这个文件不适合表格模式, 7 天内直接走 PDF。"""
    if not file_name:
        return
    try:
        r = await db.get_redis()
        await r.set(pdf_key(file_name), "1", ex=7 * 86400)
    except Exception:
        pass


def _slide_count(data: bytes, ext: str) -> int:
    """数 PPTX 有多少页（只对 OOXML 有效；老 .ppt 二进制格式返回 0 表示"不知道"）。"""
    if ext not in ("pptx", "ppsx", "pptm", "ppsm"):
        return 0
    try:
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return sum(1 for n in z.namelist()
                       if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
    except Exception:
        return 0


def _part_key(fk: str) -> str:
    return "kk:part:" + fk


async def _set_partial(fk: str, cur: int, total: int) -> None:
    """记下"当前只是前 cur 页的部分版"，供预览页显示提示。转换完就删。"""
    try:
        r = await db.get_redis()
        await r.set(_part_key(fk), "%d/%d" % (cur, total), ex=7200)
    except Exception as exc:
        log.warning("partial 标记写入失败(%s): %s", fk, exc)


async def _clear_partial(fk: str) -> None:
    try:
        r = await db.get_redis()
        await r.delete(_part_key(fk))
    except Exception:
        pass


async def partial_of(fk: str) -> str:
    """返回 "已转页数/总页数"（如 "15/119"）；完整版或未知返回空串。"""
    try:
        r = await db.get_redis()
        v = await r.get(_part_key(fk))
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        return v or ""
    except Exception:
        return ""


# ---------------- 预览缓存有效期（默认 30 分钟） ----------------

def artifact_path(file_key: str) -> str:
    """该文件当前的转换产物（PDF / 表格 HTML 或快照 / CAD 图纸 PDF / 压缩包清单）。没有产物返回空串。

    **这份清单就是缓存机制的入口**：`cache_expired()`（30 分钟到期）和
    `changed_locally()`（每次打开比对源文件）都靠它找产物，漏一个后缀，
    那类文件就永远不会过期、也永远发现不了源文件被覆盖。实测漏过 `.univsnap.json`：
    9 个"尺寸虚高但快照可用"的表格只有快照没有 HTML，源文件换了仍一直显示旧快照。

    STEP 的网格（.stepmesh.gz）**故意不列进来**：通用过期流程会先 invalidate()
    删产物再重转，而网格动辄几十 MB、重建要 20 秒以上 —— 用户在这期间什么都没有。
    它走 main._refresh_if_expired 里的 STEP 专分支：后台排队重建，新网格原子替换旧文件。
    """
    from . import cad_view, sheet
    for p in (sheet.local_pdf_path(file_key), sheet.sheet_path(file_key),
              cad_view.pdf_path(file_key),
              os.path.join(sheet.SHEET_DIR, file_key + ".univsrc"),
              os.path.join(sheet.SHEET_DIR, file_key + ".ppt.pdf"),
              sheet.snap_path(file_key),
              arc_cache_path(file_key)):
        if os.path.exists(p):
            return p
    return ""


def cache_age(file_key: str, art: str = None) -> float:
    """产物生成至今的秒数；没有产物返回 -1。

    直接用文件 mtime 判断，不在 Redis 里另记时间戳：
    产物是"原子替换"写入的，mtime 就是转换完成时刻，不会因为读操作被刷新
    （如果记在 Redis 上，is_ready() 的补写逻辑会不断续期，TTL 就永远不到）。

    art 可以由调用方传进来：一次打开会同时问"过期了吗"和"源变了吗"，
    两边都要走 artifact_path()（7 个 exists），传一份省一次。
    """
    p = artifact_path(file_key) if art is None else art
    if not p:
        return -1.0
    try:
        return max(0.0, time.time() - os.path.getmtime(p))
    except OSError:
        return -1.0


async def cache_expired(file_key: str, art: str = None) -> bool:
    """产物超过 CACHE_TTL_MINUTES 视为过期 → 需要重新下载源文件、重新转换。

    只对"网关自己生成产物"的类型有意义；PDF/图片/文本这类直接读源站的
    没有产物（cache_age 返回 -1），每次打开本来就是最新内容。
    """
    ttl = config.CACHE_TTL_SECONDS
    if ttl <= 0:
        return False
    age = cache_age(file_key, art)
    return 0 <= ttl < age


def pdf_ver(file_key: str) -> int:
    """PDF 版本号（取 mtime 秒）。前端用它判断"后台是否已经换成了新文件"。"""
    from . import sheet
    p = sheet.local_pdf_path(file_key)
    try:
        return int(os.path.getmtime(p))
    except OSError:
        return 0


# ---------------- 源文件指纹：过期不是"必须重转"，而是"必须确认没变" ----------------

def _fp_key(file_key: str) -> str:
    return "kk:srcfp:" + (file_key or "")


async def source_fingerprint(file_url: str) -> str:
    """取源站的"文件身份"：Content-Length + Last-Modified + ETag。

    用 HEAD 只取响应头，不下载文件（47MB 的 STEP 也就几十毫秒）。
    源站不支持 HEAD 时退回 GET Range: bytes=0-0，同样只拿头。
    """
    client = await http()
    try:
        r = await client.head(file_url, timeout=10)
        if r.status_code >= 400:
            r = await client.get(file_url, headers={"Range": "bytes=0-0"}, timeout=10)
        if r.status_code >= 400:
            return ""
        h = r.headers
        parts = [h.get("content-length", ""), h.get("last-modified", ""),
                 h.get("etag", "")]
        if not any(parts[:2]) and not parts[2]:
            return ""
        return "|".join(parts)
    except Exception:
        return ""


async def save_fingerprint(file_key: str, file_url: str) -> str:
    """转换成功时记下源文件指纹，供以后判断"到底变了没有"。

    **写入和校验必须走同一路**：revalidate 里优先 local_fingerprint，
    这里也必须优先它。否则会出现"写的是 HTTP 头（带 ETag/三段）、
    读的是本地 stat（两段）"，两者永远不相等 → 每次过期都判定"文件变了" →
    20 秒重转，白瞎了缓存。
    """
    fp = local_fingerprint(file_url) or await source_fingerprint(file_url)
    if not fp:
        return ""
    try:
        r = await db.get_redis()
        await r.set(_fp_key(file_key), fp, ex=86400 * 30)
    except Exception:
        pass
    return fp


async def get_fingerprint(file_key: str) -> str:
    try:
        r = await db.get_redis()
        return await r.get(_fp_key(file_key)) or ""
    except Exception:
        return ""


def _fp_size(fp: str) -> str:
    """指纹里的"文件大小"那一段。

    两种口径的第一段都是字节数：本地 `size|mtime|`，HTTP `len|last-mod|etag`。
    用它来区分"只是口径变了"和"内容真的变了"——口径切换时大小不变，
    而内容换掉大概率会变，比单看 mtime 可靠。
    """
    return (fp or "").split("|")[0]


async def changed_locally(file_key: str, file_url: str, art: str = None) -> bool:
    """源文件被原地覆盖了吗 —— 只看本地副本，不碰网络。

    为什么需要单独一条：`cache_expired()` 要等 TTL 到了才放行 `revalidate()` 比指纹，
    于是"文件刚被覆盖、缓存还在有效期内"这段时间，用户一直看到旧内容。
    受控文件都挂在 /cephfs 上，stat 一次是微秒级，所以可以**每次打开都比**。

    两种判断：
      1. 有指纹（网关转过并记过 kk:srcfp）→ 比指纹，最准
      2. 没指纹（预转产物、老记录）→ 退回比 mtime：源文件比产物新就说明没转过

    纯 HTTP 源站（地址里没有 ?path=）这里返回 False，仍按 TTL 走 ——
    每次打开都 HEAD 一次会给源站添压，也没必要。
    """
    if not config.CACHE_REVALIDATE:
        return False
    lp = local_path_of(file_url)
    if not lp:
        return False                      # 没有本地副本，交给 TTL
    old = await get_fingerprint(file_key)
    if old:
        cur = local_fingerprint(file_url)
        if not cur or cur == old:
            return False
        # 指纹不一致有两种可能：源真改了，或者"指纹口径变了"。
        # 后一种在把 /files/ 直链映射到本地之后必然出现一次：那批文件以前的指纹存的是
        # HTTP 头（size|Last-Modified|ETag），现在算出来的是本地 stat（size|mtime|），
        # 两者永远不等 —— 不识别的话，这些文件会"每打开一次就重转一次"，
        # 用户每次都得等一遍（CAD 20 秒）。
        # 判据同 revalidate()：大小一致 + 产物不比源文件旧 → 只是口径变了。
        # 大小不一致说明内容换了，一律按"变了"处理（不能只看 mtime：
        # 源在转换完成的同一秒被覆盖时，mtime 会看起来一样新）。
        art = artifact_path(file_key) if art is None else art
        try:
            if (art and _fp_size(old) == _fp_size(cur)
                    and os.path.getmtime(lp) <= os.path.getmtime(art) + 1):
                await save_fingerprint(file_key, file_url)
                return False
        except OSError:
            pass
        return True
    # 没有指纹：用"源文件比产物新"兜底（预转产物就走这条路）
    art = artifact_path(file_key) if art is None else art
    if not art:
        return False
    try:
        # +1 秒容忍：刚写完的文件与产物 mtime 可能落在同一秒
        return os.path.getmtime(lp) > os.path.getmtime(art) + 1
    except OSError:
        return False


def local_path_of(file_url: str) -> str:
    """把业务地址映射成本地挂载的文件路径。支持两种形态：

      1. `.../api/file/raw/<名字>?path=<相对受控文件/>`  → CEPH_ROOT/<rel>
      2. `http://127.0.0.1:8090/files/<相对 fileManagement/>` → CEPH_FILES_ROOT/files/<rel>

    形态 1 的 path 是调用方传的，`..` 能穿到受控文件目录外面；形态 2 的路径直接
    写在 URL 里，同样可以带 `..`。两种都做 realpath 前缀校验，不合格返回空串，
    调用方退回 HTTP。

    为什么补形态 2：它占业务请求的大头（近 30 天 4377 次），但之前映射不出来，
    于是"源文件被覆盖后立刻重转"对它完全失效，只能等 30 分钟 TTL 到期。
    """
    try:
        u = urlparse(file_url)
        rel = (urllib.parse.parse_qs(u.query).get("path") or [""])[0]
        if rel:
            root = os.path.realpath(config.CEPH_ROOT)
            p = os.path.realpath(os.path.join(root, rel.lstrip("/")))
        else:
            host = (u.netloc or "").lower()
            path = urllib.parse.unquote(u.path or "")
            if not path.startswith("/files/") or host not in config.CEPH_FILES_HOSTS:
                return ""
            root = os.path.realpath(config.CEPH_FILES_ROOT)
            p = os.path.realpath(os.path.join(root, path.lstrip("/")))
        if not p.startswith(root + os.sep):
            return ""
        return p
    except (OSError, ValueError):
        return ""


def local_fingerprint(file_url: str) -> str:
    """直接从挂载的文件系统取指纹（网关挂了 /cephfs_data 只读）。

    为什么需要这条：预转换的文件在库里存的是**不带签名**的业务地址
    （签名 5 分钟就过期，存下来没意义），拿它去 HEAD 源站只会得到 403。
    于是"源文件没变就续期"这条规则对预转文件永远不成立 —— 30 分钟后又退回重转。
    文件就在本机，直接 stat 比 HTTP 头更准，也不依赖签名。
    """
    p = local_path_of(file_url)
    if not p:
        return ""
    try:
        st = os.stat(p)
    except OSError:
        return ""
    return "%d|%s|" % (st.st_size, formatdate(st.st_mtime, usegmt=True))


def local_bytes(file_url: str):
    """读本地副本，读不到返回 None。

    用途：业务接口（127.0.0.1:8080/api/file/raw）要用户会话，服务端去 GET 是 403，
    但受控文件本身就挂在 /cephfs 上 —— 缓存过期要重转时，明明文件就在手边，
    却因为"HTTP 下载失败"把一份能看的旧产物删掉、还转不回来。
    有了这条，重转可以绕过业务接口的鉴权。
    """
    p = local_path_of(file_url)
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            return f.read()
    except OSError:
        return None


async def source_reachable(file_url: str) -> bool:
    """源站现在能不能真的给出文件内容 —— 只用来回答"能不能删掉本地缓存去重转"。

    **判据必须严，因为它的后果是不可逆的**：判"可达"就会 invalidate() 删产物，
    然后重新下载；源站这时候其实给不出文件，用户从"能看（旧缓存）"变成"打不开"，
    而且要等源站恢复才可能回来。

    以前这里只要求 `source_fingerprint()` 非空 —— 那是"响应头里有没有
    Content-Length/Last-Modified/ETag"，只要通就算，连 200 的错误页都算。
    实测某业务接口 HEAD 返回 501、GET 返回 403，某次被误判成可达，
    产物被删、重转 403 失败，文件就此打不开。

    现在改成复用 probe_file：要求 status < 400 **且**真有内容（它用
    Range: bytes=0-79 只读文件头，不下载整份）。探测无结论（超时/网络异常，
    status=0）时按**不可达**处理 —— 宁可继续用旧缓存，也不删掉唯一那份。
    """
    if local_fingerprint(file_url):
        return True
    ok, status, _detail = await probe_file(file_url)
    if status == 0:
        return False                       # 探测无结论：保守，不删缓存
    return ok


def touch_artifacts(file_key: str) -> int:
    """把该文件所有产物的 mtime 改成现在 = 续期。

    产物 mtime 就是缓存年龄的依据（见 cache_age），所以续期不需要动 Redis，
    也不用改数据库；下次 30 分钟到期时会再校验一次源站。
    """
    from . import sheet
    hit = 0
    now = time.time()
    try:
        names = os.listdir(sheet.SHEET_DIR)
    except OSError:
        return 0
    prefix = (file_key or "") + "."
    for n in names:
        if not n.startswith(prefix):
            continue
        try:
            os.utime(os.path.join(sheet.SHEET_DIR, n), (now, now))
            hit += 1
        except OSError:
            pass
    return hit


async def revalidate(file_key: str, file_url: str) -> bool:
    """缓存过期时先问一句"源文件变了吗"。

    没变 → 续期，用户继续秒开；变了（或判断不了）→ 返回 False，照旧重转。

    这一步是 CAD 体验的关键：DWG 转 PDF 一次 19~30 秒（Aspose 逐条线段描边），
    而图纸通常几周才改一次。原来每 30 分钟到期就整轮重转，等于每隔半小时
    让第一个打开的人白等 20 秒，看板上"平均 20 秒"就是这么来的。
    """
    if not config.CACHE_REVALIDATE:
        return False
    old = await get_fingerprint(file_key)
    if not old:
        return False
    # 先看本地文件（预转文件只有这条路可用），再退回 HTTP 头
    new = local_fingerprint(file_url) or await source_fingerprint(file_url)
    if not new:
        return False
    if new != old:
        # 对不上有两种可能：源真的改了，或者"指纹口径变了"。
        # 后者是补 /files/ 直链映射时必然出现的一次性现象：那批文件以前取不到本地路径，
        # 指纹存的是 HTTP 头（size|Last-Modified|ETag），现在存的是本地 stat（size|mtime|），
        # 两者永远不相等 —— 不兜底的话，全部 /files/ 文件会在第一次到期时白重转一遍
        # （CAD 一次 20 秒）。
        # 判据用产物与源文件的 mtime：产物比源新 → 源没动，只是口径变了，
        # 把新口径记下来并续期；源比产物新 → 真改了，照旧返回 False 去重转。
        lp, art = local_path_of(file_url), artifact_path(file_key)
        if not (lp and art and os.path.exists(lp)):
            return False
        try:
            if (os.path.getmtime(lp) > os.path.getmtime(art) + 1
                    or _fp_size(old) != _fp_size(new)):
                return False          # 源更新，或大小变了 → 真重转
        except OSError:
            return False
        await save_fingerprint(file_key, file_url)
        log.info("指纹口径已切换为本地 stat，更新后续期(%s)", file_key)
    n = touch_artifacts(file_key)
    if n:
        log.info("源文件未变化，缓存续期(%s): %d 个产物", file_key, n)
    return bool(n)


async def invalidate(file_key: str, reason: str = "") -> bool:
    """清掉某个文件的所有转换产物与就绪标记，让下次访问重新走一遍。

    用于"缓存超过 30 分钟"：源站文件可能已经被替换，
    必须重新下载 + 重新转换，不能继续拿 30 分钟前的 PDF 顶着。
    """
    from . import sheet
    pdf = sheet.local_pdf_path(file_key)
    from . import cad_view
    hit_png = cad_view.clear_pngs(file_key)
    # Univer Slides 的每页位图 + 压缩包内文件的临时 PDF
    try:
        for n in os.listdir(sheet.SHEET_DIR):
            if n.startswith(((file_key or "") + ".upt_p",
                             (file_key or "") + ".arc-",
                             (file_key or "") + ".arc.")):
                os.remove(os.path.join(sheet.SHEET_DIR, n))
                hit_png += 1
    except OSError:
        pass
    targets = [pdf, pdf + ".part", pdf + ".full",
               cad_view.pdf_path(file_key), cad_view.pdf_path(file_key) + ".part",
               os.path.join(sheet.SHEET_DIR, file_key + ".univsrc"),
               os.path.join(sheet.SHEET_DIR, file_key + ".univsrc.part"),
               os.path.join(sheet.SHEET_DIR, file_key + ".univsrc.xlsx"),
               os.path.join(sheet.SHEET_DIR, file_key + ".stepmesh.gz"),
               os.path.join(sheet.SHEET_DIR, file_key + ".stepmesh"),
               os.path.join(sheet.SHEET_DIR, file_key + ".stepmesh.empty"),
               os.path.join(sheet.SHEET_DIR, file_key + ".univsnap.json"),
               os.path.join(sheet.SHEET_DIR, file_key + ".univsnap.json.part"),
               os.path.join(sheet.SHEET_DIR, file_key + ".ppt.pdf"),
               os.path.join(sheet.SHEET_DIR, file_key + ".ppt.pdf.src.pptx"),
               arc_cache_path(file_key), arc_cache_path(file_key) + ".part",
               sheet.sheet_path(file_key),
               sheet.sheet_path(file_key, "simple"),
               sheet.sheet_path(file_key, "expand"),
               outline_path(file_key), src_cache_path(file_key),
               src_xlsx_path(file_key)]
    hit = 0
    for p in targets:
        try:
            os.remove(p)
            hit += 1
        except OSError:
            pass
    try:
        r = await db.get_redis()
        await r.delete(_rk(file_key))
        await r.delete(_part_key(file_key))
        await r.delete(fail_key(file_key))
        # 产物都作废了，快照也能重做一次：清掉"快照生成不了"的标记，
        # 源文件换成能解析的版本时才有机会回到 Univer
        await r.delete(_NOSNAP_KEY % file_key)
    except Exception:
        pass
    # 任务表里的旧 ready 行也要清：is_ready() 会用它们兜底判断"已就绪"
    try:
        await db.execute("DELETE FROM preview_task WHERE file_key=%s", (file_key,))
    except Exception as exc:
        log.warning("清理旧任务记录失败(%s): %s", file_key, exc)
    log.info("预览缓存已失效重建(%s): 删除 %d 个产物 + %d 张位图, 原因: %s",
             file_key, hit, hit_png, reason or "缓存过期")
    return hit > 0


async def _text_fallback(file_url: str, file_key: str, file_name: str, ext: str):
    """OOXML 文字降级：排版和图片会丢，但至少内容能看。

    只在 LibreOffice 26.8 转不出 PDF 时用；不再回落到引擎内置的 7.3。
    老版 .ppt/.doc 是 OLE2 不是 zip，先升级成 OOXML 再解析，否则
    zipfile 会抛 `File is not a zip file`，把"降级"变成"报错"。
    """
    from . import office_text
    from . import sheet as sheet_mod
    client = await http()
    fr = await _get_source(client, file_url)
    if fr.status_code >= 400:
        raise RuntimeError("文字降级时下载源文件失败 HTTP %s" % fr.status_code)
    data, use_ext = fr.content, ext
    if not is_zip(data):
        if ext in LEGACY_PPT_EXTS:
            data, use_ext = await _upgrade_to_ooxml(data, ext, "pptx", file_key), "pptx"
        elif ext in LEGACY_DOC_EXTS:
            data, use_ext = await _upgrade_to_ooxml(data, ext, "docx", file_key), "docx"
        else:
            raise RuntimeError(not_ooxml_msg(ext))
    page = office_text.extract(data, file_name, use_ext)
    os.makedirs(sheet_mod.SHEET_DIR, exist_ok=True)
    with open(sheet_mod.sheet_path(file_key), "w", encoding="utf-8") as f:
        f.write(page)
    await mark_ready(file_key, mode="sheet")


async def convert_task(file_key: str, file_url: str, task_id: str, file_name: str,
                       engine_url: str = ""):
    """后台转换: 真正触发引擎转码, 完成后标记 ready。

    file_url   : 源站语义地址(表格模式自己下载时用)
    engine_url : 给 kkFileView 用的二次编码地址(PDF 模式)
    """
    await db.execute(
        "UPDATE preview_task SET status='converting', updated_at=NOW() WHERE task_id=%s",
        (task_id,))
    started = time.time()
    try:
        async with sem():
            rendered = False
            # 就绪后写哪种模式：CAD 会把 PDF 拉到本地并标 "cad"（走位图查看器），
            # 其余仍是 "pdf"（浏览器内置 pdf.js）。
            final_mode = "pdf"
            # 该文件此前被判定不适合表格模式 → 直接走 PDF, 不再解析一轮
            if _is_direct_render(file_url) and not await pdf_preferred(file_name):
                from . import sheet
                try:
                    as_pdf = False
                    if config.XLSX_HTML and ext_of(file_url) == "xlsx":
                        as_pdf = await _render_xlsx_html(file_url, file_key, file_name)
                    elif config.MD_HTML and ext_of(file_url) in config.MD_EXTS:
                        await _render_markdown(file_url, file_key, file_name)
                    elif (config.ARCHIVE_RENDER
                          and ext_of(file_url) in config.ARCHIVE_EXTS):
                        await _render_archive(file_url, file_key, file_name)
                    else:
                        await sheet.build(file_url, file_key, file_name, ext_of(file_url))
                    await mark_ready(file_key, mode="pdf_local" if as_pdf else "sheet")
                    rendered = True
                except Exception as sheet_err:
                    # 表格/压缩包渲染失败(超时/文件过于复杂) → 回退 PDF/引擎模式，
                    # 不让任务无限挂着
                    if ext_of(file_url) in config.SVG_EXTS:
                        raise      # SVG 引擎本身不支持, 无路可退
                    # 记住这个文件: 下次直接走 PDF, 免去"先解析一轮再回退"的白等
                    from .sheet import SheetTooHeavy
                    if isinstance(sheet_err, SheetTooHeavy):
                        await mark_pdf_preferred(file_name)
                    log.warning("网关本地渲染放弃(%s), 回退: %s", file_key, sheet_err)
            ext0 = ext_of(file_url)
            lo_err = None
            if not rendered:
                # 唯一的 Office 转换引擎：网关直接调 LibreOffice 26.8 命令行。
                # 引擎内置的 7.3 不再参与任何转换（它转"多页 + 多 SmartArt"的 PPT
                # 会 SIGABRT；而 4.1.0 的 jodconverter 又只认 7.3，接不上 26.8）。
                if ext0 in config.LOCAL_CONVERT_EXTS and office_pdf.available():
                    try:
                        client = await http()
                        fr = await _get_source(client, file_url)
                        if fr.status_code >= 400:
                            raise RuntimeError("下载源文件失败 HTTP %s" % fr.status_code)
                        # 企业文档加密系统包裹的文件：LibreOffice 只会给出
                        # "source file could not be loaded"，然后白等几十秒。
                        # 这里一秒内判定并直接失败，让用户看到明确原因。
                        enc = office_pdf.encryption_of(fr.content, ext0)
                        if enc:
                            raise office_pdf.EncryptedSource("ENC:" + enc)
                        from . import sheet as _sheet
                        os.makedirs(_sheet.SHEET_DIR, exist_ok=True)
                        out_pdf = _sheet.local_pdf_path(file_key)
                        total = _slide_count(fr.content, ext0)
                        first_n = config.PPT_PARTIAL_PAGES
                        if (total > config.PPT_PARTIAL_MIN and 0 < first_n < total):
                            # 大 PPT 两阶段：先只转前 N 页，让用户十秒内看到真实排版；
                            # 同一个任务里再接着转完整版，转完原子替换整个 PDF。
                            # （LibreOffice 打开 42MB/119 页的文件本身就要 8 秒，
                            #   所以"每转一页就出一页"做不到，这是能拿到的最优解）
                            tmp = out_pdf + ".part"
                            cost = await office_pdf.to_pdf(
                                fr.content, ext0, tmp, page_range="1-%d" % first_n)
                            # 先写标记再让文件可见：否则前端正好在这一瞬间打开，
                            # 会拿到「已 ready 但没有 partial」的响应，横幅就不显示
                            await _set_partial(file_key, first_n, total)
                            os.replace(tmp, out_pdf)
                            await mark_ready(file_key, mode="pdf_local")
                            log.info("PPT 首页优先(%s): 前 %d/%d 页 %.1fs 已就绪",
                                     file_key, first_n, total, cost)
                            tmp2 = out_pdf + ".full"
                            cost2 = await office_pdf.to_pdf(fr.content, ext0, tmp2)
                            os.replace(tmp2, out_pdf)
                            await _clear_partial(file_key)
                            log.info("PPT 完整版完成(%s): 共 %d 页 %.1fs",
                                     file_key, total, cost2)
                        else:
                            cost = await office_pdf.to_pdf(fr.content, ext0, out_pdf)
                            await mark_ready(file_key, mode="pdf_local")
                            log.info("本地转换完成(%s) %s 耗时 %.1fs", file_key, ext0, cost)
                        rendered = True
                    except office_pdf.EncryptedSource:
                        raise            # 加密文件没有降级余地，直接失败并记原因
                    except Exception as exc:
                        lo_err = exc
                        log.warning("26.8 转换失败(%s) %s: %s", file_key, ext0, exc)

            if not rendered and ext0 not in config.OFFICE_EXTS:
                # 非 Office（PDF/图片/CAD/压缩包等）才交给引擎
                try:
                    client = await http()
                    t_engine = time.time()
                    # CAD 类这一步是"真的在转"：引擎的 CAD 页面会同步把 DWG 转成
                    # PDF/SVG（实测 146KB 图纸 12.7 秒，图纸越大越久），所以必须给足超时，
                    # 而且要在后台先跑完 —— 用户看到的就是"转换中"，转好再秒开。
                    resp = await client.get(engine_preview_url(engine_url or file_url),
                                            timeout=config.CONVERT_TIMEOUT)
                    if resp.status_code >= 400:
                        raise RuntimeError("引擎返回 %s" % resp.status_code)
                    page_err = engine_page_error(resp.text)
                    if page_err:
                        raise RuntimeError(page_err)
                    if ext0 in config.CAD_EXTS:
                        log.info("CAD 转换完成(%s) %s %.1fs", file_key, ext0,
                                 time.time() - t_engine)
                        # 引擎的 PDF 有几十 MB，且每条线段是一次独立描边：
                        # pdf.js 渲染这种图要几十秒到几分钟（实测 246s 还没画完）。
                        # 拉到网关本地，改用 PDFium 出位图（整页 6000px 只要 0.8s）。
                        if config.CAD_RASTER:
                            from . import cad_view
                            pdf_url = cad_view.pdf_url_of_page(resp.text)
                            if pdf_url and await cad_view.cache_pdf(pdf_url, file_key):
                                final_mode = "cad"
                                log.info("CAD 已切换位图模式(%s): %s", file_key, pdf_url)
                            elif pdf_url:
                                log.warning("CAD PDF 缓存失败, 继续走 pdf.js(%s)", file_key)
                    rendered = True
                except Exception as conv_err:
                    # 兜底1: 引擎缺少该格式的过滤器时，用本机 26.8 再试一次
                    if (ext0 in config.LOCAL_FALLBACK_EXTS and office_pdf.available()
                            and ext0 not in config.LOCAL_CONVERT_EXTS):
                        try:
                            client = await http()
                            fr = await _get_source(client, file_url)
                            if fr.status_code >= 400:
                                raise RuntimeError(
                                    "下载源文件失败 HTTP %s" % fr.status_code)
                            from . import sheet as _sheet
                            os.makedirs(_sheet.SHEET_DIR, exist_ok=True)
                            cost = await office_pdf.to_pdf(
                                fr.content, ext0, _sheet.local_pdf_path(file_key))
                            await mark_ready(file_key, mode="pdf_local")
                            log.warning("引擎转换失败, 已用本地 LibreOffice 26.8 转过"
                                        "(%s) 耗时 %.1fs, 原因: %s",
                                        file_key, cost, conv_err)
                            rendered = True
                        except Exception as lo_err2:
                            log.warning("本地兜底也失败(%s): %s", file_key, lo_err2)
                    # 兜底2: 连 26.8 都转不出 → 提取 OOXML 文字, 渲染成阅读页
                    if not rendered:
                        if ext0 not in TEXT_FALLBACK_EXTS:
                            raise
                        await _text_fallback(file_url, file_key, file_name, ext0)
                        log.warning("PDF 转码失败, 已降级为文字模式(%s): %s",
                                    file_key, conv_err)
                        rendered = True

            if not rendered and ext0 in config.OFFICE_EXTS:
                # Office 只认 26.8：转不出来就文字降级，绝不回落引擎的 7.3
                if ext0 not in TEXT_FALLBACK_EXTS:
                    raise lo_err or RuntimeError(
                        "LibreOffice 26.8 未能转换 %s 文件" % ext0)
                await _text_fallback(file_url, file_key, file_name, ext0)
                log.warning("26.8 转换失败, 已降级为文字模式(%s): %s", file_key, lo_err)
                rendered = True

        await db.execute(
            "UPDATE preview_task SET status='ready', ready_at=NOW(), updated_at=NOW(), "
            "err_msg='' WHERE task_id=%s", (task_id,))
        await mark_ready(file_key, mode=final_mode)
        # 记下源文件指纹（大小/最后修改/ETag）。30 分钟后缓存到期时先比这个，
        # 没变就直接续期，不再把 DWG 重转一遍（那是 19~30 秒的白等）。
        await save_fingerprint(file_key, file_url)
        # PDF 已就绪: 快速内容页和缓存的源文件没用了, 及时删掉(42MB/份, 不删会撑爆磁盘)
        if ext_of(file_url) in ("ppt", "pptx"):
            for p in (outline_path(file_key), src_cache_path(file_key)):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
    except Exception as exc:
        await db.execute(
            "UPDATE preview_task SET status='failed', err_msg=%s, retry_cnt=retry_cnt+1, "
            "updated_at=NOW() WHERE task_id=%s", (str(exc)[:250], task_id))
        # 记住失败原因, 下次调用直接告知, 不再让用户白等一轮。
        # 连接/超时是瞬时故障（源站实例被回收、端口漂移），冻 5 分钟只会让监控
        # 连刷 422；60 秒后就允许再试，真实坏文件仍走 5 分钟。
        _msg = str(exc)[:200]
        _low = _msg.lower()
        _ttl = 60 if ("connection" in _low or "connect" in _low
                      or "timed out" in _low) else 300
        await mark_failed(file_key, _msg, ttl=_ttl)
    finally:
        cost = int((time.time() - started) * 1000)
        await db.execute(
            "UPDATE preview_task SET updated_at=NOW() WHERE task_id=%s", (task_id,))
        # 任务结束(成功/失败)都要释放抢占, 否则后续请求会一直复用一个死任务。
        # 带上 task_id：只释放自己那把，避免误删别人刚抢到的锁。
        await release_task(file_key, task_id)
        return cost


async def add_task(file_url: str) -> str:
    """调用引擎转码队列接口预热。"""
    client = await http()
    url = "%s/addTask?url=%s" % (config.KK_BASE, encode_url(file_url))
    resp = await client.get(url, timeout=30)
    return resp.text[:120]
