# -*- coding: utf-8 -*-
"""文件身份（缓存键）计算 —— 纯标准库，网关和预转脚本共用这一份。

为什么单独一个模块：这个函数在 `gateway/app/engine.py` 和 `tools/preconvert.py`
里各存了一份副本，改一处忘另一处，就会出现"预转按这个键落盘、用户点开按那个键
查找"的错位 —— 预转天天跑，命中率却是 0。放这里，两边 import 同一份。

**这个键必须只取决于"是哪个文件"**，任何每次变化的成分都会让缓存彻底失效：

1. 签名参数（exp/sig/token/...）—— 每次打开都重新生成；
2. 路径里的一次性令牌段 `/t/<40位hex>/` —— 知识库把令牌放路径里，不在 query；
3. 同一服务的多种取文件形态：
       /api/file/raw?path=X
       /api/file/raw/名字.pdf?path=X&exp=..&sig=..
   前者 path 是 `/api/file/raw`，后者多一段文件名，同一个文件就变成两个键。

实测（2026-11-12）：3744 个文件占了 4497 个键，352 个文件被存成 1105 份，
多出 753 份重复缓存，每份都是完整转换 —— PPT 一份 200~333 秒。
"""
import hashlib
import re
import urllib.parse

# 签名/时效类参数：值每次都变，剔除后不影响文件身份
VOLATILE_PARAMS = frozenset((
    "exp", "expires", "sig", "sign", "signature", "token", "access_token",
    "auth", "authorization", "timestamp", "ts", "_t", "nonce",
    "x-amz-signature", "x-amz-credential", "x-amz-date", "x-amz-expires",
    "x-amz-security-token", "awsaccesskeyid", "ossaccesskeyid",
))

# 路径里的令牌段：`/t/6af30fae.../文件名`，中间是 16 位以上纯十六进制
PATH_TOKEN_RE = re.compile(r"(?<=/)(?:t|token|tmp|file|doc)/([0-9a-fA-F]{16,})(?=/)")

# 这些参数的值**就是**业务相对路径，比 URL 尾段的文件名更权威
PATH_PARAMS = ("path", "file", "filepath", "file_path", "filename")


def _norm_token_path(path: str) -> str:
    """把路径里的一次性令牌抹成固定占位，保留其余结构。"""
    return PATH_TOKEN_RE.sub(lambda m: m.group(0).replace(m.group(1), "<token>"), path)


def identity(url: str) -> str:
    """返回用于算键的**规范化身份串**（调试时打印它比看哈希有用）。"""
    raw = (url or "").strip()
    u = urllib.parse.urlparse(raw)
    pairs = [(k, v) for k, vs in urllib.parse.parse_qs(
        u.query, keep_blank_values=True).items()
             if k.lower() not in VOLATILE_PARAMS for v in vs]
    pairs.sort()
    scheme_host = "%s://%s" % (u.scheme.lower(), u.netloc.lower())
    biz = [(k, v) for k, v in pairs if k.lower() in PATH_PARAMS]
    if biz:
        # 业务路径唯一确定文件；同名服务的不同"皮"（/raw 还是 /raw/名字.pdf）都归一到它
        rest = [(k, v) for k, v in pairs if k.lower() not in PATH_PARAMS]
        key = "%s|%s" % (scheme_host, urllib.parse.urlencode(biz))
        if rest:
            key += "|" + urllib.parse.urlencode(rest)
        return key
    key = scheme_host + _norm_token_path(u.path)
    query = urllib.parse.urlencode(pairs)
    if query:
        key += "?" + query
    return key


def of(url: str) -> str:
    """文件身份 → 64 位十六进制缓存键。解析失败时退回原始串，不抛异常。"""
    raw = (url or "").strip()
    try:
        base = identity(raw)
    except Exception:
        base = raw
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
