# -*- coding: utf-8 -*-
"""压缩包内部文件的统一读取层：zip / tar / rar / 7z。

为什么要这一层：
    清单页以前只是"列名字"，用户点不进任何文件。现在要支持"点开压缩包里的
    xlsx/pdf/图片直接看"，就得能按条目名把字节取出来。

各格式的读取器：
    zip      → 标准库 zipfile（顺便还原 GBK 文件名）
    tar      → 标准库 tarfile
    rar/rar5 → 7-Zip 静态二进制（libarchive / unrar 在本机装不上，见下）
    7z/gz    → 同上

为什么用 7zz 而不是 unrar：
    网关容器是 python:3.11-slim，`apt-get update` 在运行期连不上 Debian 源，
    p7zip / libarchive-tools 都装不进去。7-Zip 官方提供静态单文件 `7zz`
    （无动态依赖），放到 ./data/tools 挂载进容器即可，支持 zip/7z/rar/rar5/gz。
    二进制缺失时 rar/7z 自动退回"只列清单"（由引擎出页面），不会报错。
"""
import io
import os
import re
import shutil
import subprocess
import tarfile
import time
import zipfile

from .archive_list import _tar_name, _zip_name

# 7-Zip 静态二进制（compose 把 ./data/tools 挂到 /opt/kk-tools）
SEVENZIP = os.environ.get("SEVENZIP_BIN", "/opt/kk-tools/7zz")

ZIP_EXTS = ("zip", "jar", "war", "apk", "epub", "odt", "ods", "odp",
            "docx", "xlsx", "pptx", "ipa", "xpi")
# 注意这里只写单段扩展名：ext_of_name() 只取最后一段，
# "foo.tar.gz" 进来是 "gz"，走的是 7-Zip 分支（它认得 tar.gz）。
TAR_EXTS = ("tar", "tgz", "tbz2", "txz")
SZ_EXTS = ("rar", "r00", "7z", "gz", "bz2", "xz", "cab", "iso")

# 单个条目解压上限（防 zip 炸弹；正常业务文件远小于此）
MAX_ENTRY_MB = int(os.environ.get("ARCHIVE_ENTRY_MAX_MB", "300"))


class UnsupportedArchive(RuntimeError):
    """本机没有能读这种格式的工具。"""


class Entry(object):
    __slots__ = ("name", "size", "packed", "mtime", "is_dir")

    def __init__(self, name, size, packed, mtime, is_dir):
        self.name = name
        self.size = size
        self.packed = packed
        self.mtime = mtime
        self.is_dir = is_dir


# ------------------------------------------------------------------ 7-Zip
def sevenzip() -> str:
    """可用的 7-Zip 可执行文件；没有返回空串。"""
    for cand in (SEVENZIP, "/opt/kk-tools/7zz", shutil.which("7zz") or "",
                 shutil.which("7z") or "", shutil.which("7za") or ""):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return ""


def _run7z(args, timeout=120, stdout_data=False):
    exe = sevenzip()
    if not exe:
        raise UnsupportedArchive("服务端未安装 7-Zip，无法读取该格式")
    env = dict(os.environ)
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    p = subprocess.run([exe] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout, env=env)
    if p.returncode not in (0, 1):        # 1 = 有警告但通常仍可用
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("7-Zip 失败(%s): %s" % (p.returncode, err[:200]))
    return p.stdout if stdout_data else p.stdout.decode("utf-8", "replace")


def _parse_slt(text):
    """解析 `7zz l -slt` 的属性块，返回 [Entry]。

    块以空行分隔，形如：
        Path = a/b.txt
        Folder = -
        Size = 1234
        Packed Size = 567
        Modified = 2024-01-01 10:00:00
    """
    out, cur, in_body = [], {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("----------"):
            in_body = True
            continue
        if not in_body:
            continue
        if not s:
            if cur.get("Path"):
                out.append(cur)
            cur = {}
            continue
        if " = " not in s:
            continue
        k, v = s.split(" = ", 1)
        cur[k.strip()] = v.strip()
    if cur.get("Path"):
        out.append(cur)

    entries = []
    for d in out:
        name = d.get("Path", "")
        if not name:
            continue
        is_dir = (d.get("Folder") == "+") or (d.get("Attributes", "").startswith("D"))
        try:
            size = int(d.get("Size") or 0)
        except ValueError:
            size = 0
        try:
            packed = int(d.get("Packed Size") or 0)
        except ValueError:
            packed = 0
        mtime = 0
        mt = d.get("Modified") or d.get("Created") or ""
        if mt:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    mtime = time.mktime(time.strptime(mt[:19], fmt))
                    break
                except ValueError:
                    continue
        entries.append(Entry(name.replace("\\", "/"), size, packed, mtime, is_dir))
    return entries


def _sevenzip_list(path: str):
    text = _run7z(["l", "-slt", "-ba", "--", path], timeout=180)
    entries = _parse_slt(text)
    if not entries:
        # 少数版本 -ba 会把属性块也压掉，退回带表头的输出再解析一次
        text = _run7z(["l", "-slt", "--", path], timeout=180)
        entries = _parse_slt(text)
    return entries


def _sevenzip_read(path: str, name: str, limit: int) -> bytes:
    data = _run7z(["x", "-so", "-y", "--", path, name],
                  timeout=180, stdout_data=True)
    if len(data) > limit:
        raise ValueError("该文件超过在线预览上限（%d MB）" % (limit // 1048576))
    return data


# ------------------------------------------------------------------ 列表
def _zip_open(src):
    """zipfile 只认路径或文件对象，bytes 要先包成 BytesIO。

    嵌套压缩包（zip 里再套 zip）时 src 是内存里的字节，这条路必须能走通。
    """
    return zipfile.ZipFile(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)


def _zip_entries(path_or_bytes):
    z = _zip_open(path_or_bytes)
    out = []
    for it in z.infolist():
        name = _zip_name(it)
        out.append(Entry(name, it.file_size, it.compress_size,
                         _zip_time(it.date_time), it.is_dir()))
    z.close()
    return out


def _zip_time(dt):
    try:
        return time.mktime(tuple(dt) + (0, 0, -1))
    except Exception:
        return 0


def _tar_open(src):
    if isinstance(src, (bytes, bytearray)):
        return tarfile.open(fileobj=io.BytesIO(src))
    return tarfile.open(name=src)


def _tar_entries(path_or_bytes):
    tf = _tar_open(path_or_bytes)
    try:
        out = []
        for m in tf.getmembers():
            out.append(Entry(_tar_name(m).replace("\\", "/"), m.size, m.size,
                             int(m.mtime or 0), m.isdir()))
        return out
    finally:
        tf.close()


# 这些扩展名本质也是 zip，但对用户来说"是一个文档"而不是"一个压缩包"，
# 点开应该预览内容，不能展开成内部条目清单。
DOC_CONTAINER_EXTS = set("""docx docm dotx xlsx xlsm xltx pptx pptm potx ppsx
                            odt ods odp""".split())


def is_archive(name: str) -> bool:
    """这个条目该不该"进下一层"（真正的压缩包）。"""
    ext = ext_of_name(name)
    if ext in DOC_CONTAINER_EXTS:
        return False
    return kind_of(ext) != ""


def kind_of(ext: str) -> str:
    ext = (ext or "").lower().lstrip(".")
    if ext in TAR_EXTS:
        return "tar"
    if ext in ZIP_EXTS:
        return "zip"
    if ext in SZ_EXTS:
        return "7z"
    return ""


def listing(src, ext: str):
    """列出条目。src 可以是 bytes（zip/tar）或磁盘路径（任意格式）。"""
    kind = kind_of(ext)
    if kind == "zip":
        return _zip_entries(src)
    if kind == "tar":
        return _tar_entries(src)
    if kind == "7z":
        if isinstance(src, bytes):
            raise UnsupportedArchive("该格式需要先落盘才能读取")
        return _sevenzip_list(src)
    # 扩展名不认识：按魔数猜
    head = src[:8] if isinstance(src, bytes) else _head(src)
    if head[:2] == b"PK":
        return _zip_entries(src)
    if head[:5] == b"Rar!\x1a" or head[:6] == b"7z\xbc\xaf\x27\x1c":
        if isinstance(src, bytes):
            raise UnsupportedArchive("该格式需要先落盘才能读取")
        return _sevenzip_list(src)
    raise UnsupportedArchive("无法识别的压缩格式")


def _head(path: str) -> bytes:
    try:
        with open(path, "rb") as f:
            return f.read(8)
    except OSError:
        return b""


# ------------------------------------------------------------------ 读取
def read_entry(src, ext: str, name: str, limit: int = 0) -> bytes:
    """取出一个条目的原始字节。src 同 listing()。"""
    limit = limit or MAX_ENTRY_MB * 1024 * 1024
    kind = kind_of(ext)
    if kind == "zip":
        z = _zip_open(src)
        try:
            for it in z.infolist():
                if _zip_name(it) == name:
                    if it.file_size > limit:
                        raise ValueError("该文件超过在线预览上限（%d MB）"
                                         % (limit // 1048576))
                    return z.read(it)
        finally:
            z.close()
        raise KeyError(name)
    if kind == "tar":
        tf = _tar_open(src)
        try:
            for m in tf.getmembers():
                if _tar_name(m).replace("\\", "/") == name and m.isfile():
                    if m.size > limit:
                        raise ValueError("该文件超过在线预览上限（%d MB）"
                                         % (limit // 1048576))
                    f = tf.extractfile(m)
                    return f.read() if f else b""
        finally:
            tf.close()
        raise KeyError(name)
    if kind == "7z":
        if isinstance(src, bytes):
            raise UnsupportedArchive("该格式需要先落盘才能读取")
        return _sevenzip_read(src, name, limit)
    raise UnsupportedArchive("无法识别的压缩格式")


# ------------------------------------------------------------------ 工具
_TEXT_EXT = set("""
txt log csv tsv md markdown json xml yaml yml ini cfg conf properties toml
sql py js mjs cjs ts tsx jsx java kt c h cpp hpp cc cs go rs rb php sh bash
bat cmd ps1 html htm css scss less vue svelte lua pl r m matlab vb vbs asm
srt ass vtt diff patch gitignore env lst map
""".split())

IMG_EXT = set("jpg jpeg png gif bmp webp svg ico avif".split())
PDF_EXT = set("pdf".split())
AV_EXT = set("mp4 webm ogv mp3 wav m4a aac oga flac".split())
OFFICE_PDF = set("doc docx docm dot dotx rtf odt wps".split())
OFFICE_SHEET = set("xls xlsx xlsm csv et ods".split())
OFFICE_SLIDE = set("ppt pptx pptm pps ppsx odp dps".split())


def ext_of_name(name: str) -> str:
    base = (name or "").rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return base.rsplit(".", 1)[-1].lower()


def is_text(name: str) -> bool:
    return ext_of_name(name) in _TEXT_EXT


def decode_text(data: bytes, errors: str = "replace") -> str:
    """文本解码：UTF-8（含 BOM）→ GB18030 → big5 → latin-1 兜底。

    errors 默认 replace：预览时按字节截断过的大文件，末尾可能正好切在
    多字节字符中间，strict 解码会整段失败、最后退成 latin-1 满屏乱码。
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc, errors)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", "replace")


def safe_mime(name: str) -> str:
    ext = ext_of_name(name)
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
        "svg": "image/svg+xml", "ico": "image/x-icon", "avif": "image/avif",
        "pdf": "application/pdf",
        "mp4": "video/mp4", "webm": "video/webm", "ogv": "video/ogg",
        "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
        "aac": "audio/aac", "oga": "audio/ogg", "flac": "audio/flac",
        "txt": "text/plain; charset=utf-8", "csv": "text/csv; charset=utf-8",
    }.get(ext, "application/octet-stream")
