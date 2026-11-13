"""用 LibreOffice 命令行把 Office 文件转成 PDF。

为什么要绕开 kkFileView：
    kkFileView 4.1.0 内置 jodconverter，通过 UNO socket 调用 LibreOffice。
    它与 LibreOffice 26.x 的 UNO 协议不兼容，转换一律报"该文件版本不兼容"。
    所以转换这一步由网关直接 subprocess 调用，引擎只负责 PDF/图片预览。

版本策略（2026-10-22 起统一）：
    只保留 LibreOffice 26.8.1，**不再保留 7.3 任何副本**：
    - 7.3 遇到"多页 + 多 SmartArt"的 PPT 会 SIGABRT 崩溃；
    - 7.3 不认 Markdown，会把 .md 当纯文本塞进 Writer。
    网关是唯一的 Office 转换入口，7.3 的 data/lo73 副本与回退配置都已删除。
    预览引擎（kkFileView 4.1.0）自带的 7.3 只在它自己启动时被探测，
    不会再收到任何 Office 文件（见 engine.py 的 OFFICE_EXTS 判定）。

设计要点：
    - **每个执行线程复用一份 UserInstallation profile**：LO 首次初始化 profile
      要 ~0.24 秒（实测新建 0.55s vs 复用 0.31s），每次新建纯属浪费。
      线程之间用 thread-local 隔离，天然不会争同一份 profile 的锁；
      万一残锁导致失败，删掉该 profile 重试一次。
    - 并发上限（默认 2）× uvicorn worker 数 = 实际并发路数（2 worker → 4 路）
    - 每个引擎都有超时，超时直接 kill，不会把任务挂着
"""
import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
import time

# 只认 26.8.1；列表仍保留逗号分隔写法，方便以后升级版本时替换路径
CANDIDATES = [p.strip() for p in (os.environ.get(
    "LO_SOFFICE_LIST",
    "/opt/libreoffice26.8/program/soffice"
).split(",")) if p.strip()]
# 兼容旧配置
if os.environ.get("LO_SOFFICE"):
    CANDIDATES = [os.environ["LO_SOFFICE"]]

SOFFICE = CANDIDATES[0] if CANDIDATES else ""
TIMEOUT = int(os.environ.get("LO_CONVERT_TIMEOUT", "180"))
MAX_CONC = int(os.environ.get("LO_CONVERT_CONC", "2"))

# Markdown 必须用 26.8: 旧版不认 .md, 会把它当纯文本塞进 Writer
# (实测 7.3 转出来的 HTML 一个标题标签都没有, 正文里全是 "## ")。
MD_EXTS = {"md", "markdown"}
MD_SOFFICE = os.environ.get("LO_MD_SOFFICE", "/opt/libreoffice26.8/program/soffice")

_sem = None


class EncryptedSource(RuntimeError):
    """源文件被企业文档加密系统包裹，服务器拿到的不是明文。

    2026-10-23 定位：研发部共享目录里的 pptx 首字节是 `%TSD-Header-###%`，
    整份文件是密文（zipfile 都打不开）。这些文件在装了加密客户端的 PC 上是透明的，
    但服务器侧没有任何解密能力，LibreOffice 只能报 "source file could not be loaded"。
    以前这种文件会让用户对着"正在转换"等 30 多秒，最后还是一场空；
    现在下载完立刻判定，一秒内给出明确结论。
    """


# 常见企业文档加密/透明加密系统的文件头（%开头，肉眼可辨）
_DLP_MAGICS = (
    (b"%TSD-Header-###%", "TSD 文档加密"),
    (b"%TSD", "TSD 文档加密"),
    (b"%ESAFENET", "亿赛通 CDG"),
    (b"%Sangfor", "深信服 EDR"),
    (b"%DLP", "文档加密"),
)
# Office 明文的合法开头：OOXML(zip) / OLE2 复合文档 / RTF
OFFICE_MAGIC = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"{\\rtf")


def encryption_of(data: bytes, ext: str = "") -> str:
    """返回加密系统名称（空串表示是明文）。

    只对 Office/PDF 这类"必然是一种已知容器格式"的文件做兜底判定：
    头既不是 zip 也不是 OLE2/RTF，就说明被外层包裹了。
    文本类（md/txt/csv）不参与，避免把任意文本误判成加密。
    """
    head = (data or b"")[:80]
    if not head:
        return ""
    for magic, name in _DLP_MAGICS:
        if head.startswith(magic):
            return name
    ext = (ext or "").lower()
    if ext in ("docx", "docm", "xlsx", "xlsm", "pptx", "pptm", "ppsx", "potx",
               "doc", "xls", "ppt", "pps", "pot"):
        if not any(head.startswith(m) for m in OFFICE_MAGIC):
            return "未知加密容器"
    return ""


def available() -> bool:
    return any(os.path.exists(p) for p in CANDIDATES)


# 每个执行线程一份常驻 profile：省掉每次 0.2~0.25 秒的 profile 初始化
PROFILE_ROOT = os.environ.get("LO_PROFILE_DIR", "/tmp/lo_profiles")
_tls = threading.local()


def _profile() -> str:
    path = getattr(_tls, "profile", None)
    if path is None:
        path = os.path.join(PROFILE_ROOT,
                            "p%d_t%d" % (os.getpid(), threading.get_ident()))
        os.makedirs(path, exist_ok=True)
        _tls.profile = path
    return path


def _reset_profile() -> None:
    """profile 被上一位残留的锁占住时，整份删掉重建。"""
    path = getattr(_tls, "profile", None)
    if path:
        shutil.rmtree(path, ignore_errors=True)
    _tls.profile = None
    _profile()


def _semaphore():
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(MAX_CONC)
    return _sem


def _pdf_target(ext: str, page_range: str = "") -> str:
    """构造 --convert-to 的目标。

    带 page_range 时必须显式指定 filter 名（Impress/Writer/Calc 各不相同），
    否则 LibreOffice 只认默认的 pdf 过滤链、PageRange 会被忽略。
    """
    if not page_range:
        return "pdf"
    family = {"ppt": "impress", "pptx": "impress", "pps": "impress", "ppsx": "impress",
              "doc": "writer", "docx": "writer", "odt": "writer", "rtf": "writer",
              "xls": "calc", "xlsx": "calc", "ods": "calc", "csv": "calc"}.get(ext, "writer")
    return ('pdf:%s_pdf_Export:{"PageRange":{"type":"string","value":"%s"}}'
            % (family, page_range))


def _atomic_replace(src: str, dst: str) -> None:
    """把转换结果放到目标路径，**不留半截文件**。

    src 在 /tmp（容器 overlay）、dst 在 /app/sheets（bind mount），跨设备 ——
    `shutil.move` 会退化成"边拷边写"，正在看这份 PDF 的人就可能读到写了一半的内容。
    跨设备时先拷到同目录的 `.part`，再 rename，读的人只会看到完整的旧版或完整的新版。
    """
    try:
        os.replace(src, dst)                   # 同一设备：原子 rename
        return
    except OSError:
        pass
    part = dst + ".part"
    try:
        shutil.copyfile(src, part)
        os.replace(part, dst)
    finally:
        try:
            os.unlink(src)
        except OSError:
            pass


def _run_one(binary: str, data: bytes, ext: str, out_pdf: str,
             page_range: str = "", conv_to: str = "") -> float:
    """conv_to 为空表示转 PDF(默认)；给了 "pptx"/"docx" 就是做格式升级。"""
    tmpdir = tempfile.mkdtemp(prefix="lo_conv_")
    try:
        src = os.path.join(tmpdir, "src." + ext)
        with open(src, "wb") as f:
            f.write(data)
        target = conv_to or _pdf_target(ext, page_range)
        produced_name = "src." + (conv_to or "pdf")

        def _exec(profile: str) -> subprocess.CompletedProcess:
            cmd = [binary, "--headless", "--norestore", "--nologo", "--nodefault",
                   "-env:UserInstallation=file://" + profile,
                   "--convert-to", target,
                   "--outdir", tmpdir, src]
            return subprocess.run(cmd, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=TIMEOUT)

        t0 = time.time()
        proc = _exec(_profile())
        produced = os.path.join(tmpdir, produced_name)
        if not os.path.exists(produced) or os.path.getsize(produced) == 0:
            out = (proc.stdout or b"").decode("utf-8", "replace")
            # profile 残留锁 / 损坏 → 换一份干净的重试一次，别让用户白等
            if any(k in out.lower() for k in ("lock", "profile", "another instance")):
                _reset_profile()
                proc = _exec(_profile())
                out = (proc.stdout or b"").decode("utf-8", "replace")
            if not os.path.exists(produced) or os.path.getsize(produced) == 0:
                raise RuntimeError("%s 转换失败: %s"
                                   % (os.path.basename(os.path.dirname(os.path.dirname(binary))),
                                      out.strip()[:160] or "无输出"))
        cost = time.time() - t0
        _atomic_replace(produced, out_pdf)
        return cost
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def to_pdf(data: bytes, ext: str, out_pdf: str,
                 page_range: str = "") -> float:
    """把 Office 文件转成 PDF 写到 out_pdf，返回耗时秒数。

    依次尝试 CANDIDATES 里的 LibreOffice，第一个成功的即返回。
    page_range 形如 "1-15"：只导出这部分页，用于大 PPT 先出前几页。
    """
    if not available():
        raise RuntimeError("未找到 LibreOffice: %s" % ",".join(CANDIDATES))
    usable = [p for p in CANDIDATES if os.path.exists(p)]
    if ext in MD_EXTS:
        # Markdown 只认 26.8, 不能落到 7.3(会当纯文本处理)
        usable = [MD_SOFFICE] if os.path.exists(MD_SOFFICE) else []
        if not usable:
            raise RuntimeError("Markdown 预览需要 LibreOffice 26.8, 未找到 %s" % MD_SOFFICE)

    def _do() -> float:
        errors = []
        for binary in usable:
            try:
                return _run_one(binary, data, ext, out_pdf, page_range)
            except subprocess.TimeoutExpired:
                errors.append("%s 超时 %ss" % (binary, TIMEOUT))
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("; ".join(errors)[:300])

    async with _semaphore():
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, _do), timeout=(TIMEOUT + 30) * max(1, len(usable)))


async def to_ooxml(data: bytes, ext: str, out_path: str, conv_to: str) -> float:
    """老版二进制格式(.ppt/.doc/.xls)升级成 OOXML(.pptx/.docx/.xlsx)。

    为什么需要这一步：
        「快速内容页」(/prog) 和「文字降级」都是纯标准库解析 zip 结构
        （pptx 的 ppt/slides/*.xml、docx 的 word/document.xml），
        而 .ppt/.doc 是 OLE2 复合文档，根本不是 zip，
        直接丢给 zipfile 会抛 `File is not a zip file`（用户看到的就是 503）。
        实测 113 页 / 21MB 的 .ppt 升级成 pptx 只要 1.2 秒，
        比转 PDF（6 秒）快得多，所以这条路适合做"秒开内容页"。
    """
    if not available():
        raise RuntimeError("未找到 LibreOffice: %s" % ",".join(CANDIDATES))
    usable = [p for p in CANDIDATES if os.path.exists(p)]

    def _do() -> float:
        errors = []
        for binary in usable:
            try:
                return _run_one(binary, data, ext, out_path, "", conv_to)
            except subprocess.TimeoutExpired:
                errors.append("%s 超时 %ss" % (binary, TIMEOUT))
            except Exception as exc:
                errors.append(str(exc))
        raise RuntimeError("; ".join(errors)[:300])

    async with _semaphore():
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, _do), timeout=(TIMEOUT + 30) * max(1, len(usable)))
