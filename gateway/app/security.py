import base64
import hashlib
import hmac
import ipaddress
import json
import re
import time

from fastapi import Header, HTTPException, Request

from . import config


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(app_id: str, ttl: int | None = None) -> str:
    ttl = ttl or config.TOKEN_TTL
    body = {"typ": "access", "app": app_id,
            "exp": int(time.time()) + ttl, "iat": int(time.time())}
    payload = _b64e(json.dumps(body, separators=(",", ":")).encode())
    return payload + "." + _sign(payload)


def parse_token(token: str, expect: str = "access") -> dict:
    """解析并校验类型。expect: access / preview / admin。

    三种令牌共用同一个签名密钥，必须靠 `typ` 区分。原来只验签名和过期，
    于是**预览令牌可以直接当访问令牌用**：预览地址会出现在浏览器地址栏、
    iframe src、Referer 和访问日志里，拿到它就能以该 appId 身份调接口
    （实测由 tools/check_security.py 抓出）。
    """
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="缺少或非法访问令牌")
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), sig):
        raise HTTPException(status_code=401, detail="令牌签名校验失败")
    body = json.loads(_b64d(payload))
    typ = body.get("typ")
    if typ is None:
        # 上线前签发的旧令牌没有 typ：带 fk 的是预览令牌，一律不许当访问令牌
        if expect == "access" and body.get("fk"):
            raise HTTPException(status_code=401, detail="该令牌只能用于预览地址")
    elif typ != expect:
        raise HTTPException(status_code=401, detail="令牌类型不匹配")
    if body.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="令牌已过期, 请重新获取")
    return body


def make_preview_token(file_key: str, emp_no: str, file_url: str, app_id: str = "",
                       emp_name: str = "", dept: str = "", req_id: str = "") -> str:
    """预览令牌。带上 app_id, 以便把预览页的实际访问归属到调用系统。

    同时带上姓名/部门: 后面打开预览页（/preview/page、/sheet 等）时没有请求体,
    只能从令牌里取, 否则这批访问记录的姓名部门全是空的。

    req_id: 申请这次预览时的请求号。预览页靠它把"内容可见"的时刻报回来，
    换算成端到端加载耗时（见 loadmetric.py）。
    """
    body = {"typ": "preview", "fk": file_key, "emp": emp_no, "url": file_url,
            "app": app_id, "exp": int(time.time()) + config.PREVIEW_TOKEN_TTL}
    if emp_name:
        body["nm"] = emp_name[:64]
    if dept:
        body["dp"] = dept[:64]
    if req_id:
        body["rid"] = req_id
    payload = _b64e(json.dumps(body, separators=(",", ":")).encode())
    return payload + "." + _sign(payload)


def parse_preview_token(token: str) -> dict:
    if not token or "." not in token:
        raise HTTPException(status_code=401, detail="预览地址非法")
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(payload), sig):
        raise HTTPException(status_code=401, detail="预览地址签名校验失败")
    body = json.loads(_b64d(payload))
    # 旧链接没有 typ（兼容一个 TTL 周期）；有 typ 就必须是 preview
    if body.get("typ") not in (None, "preview"):
        raise HTTPException(status_code=401, detail="预览地址非法")
    if body.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="预览地址已过期, 请重新获取")
    return body


async def _load_account(app_id: str, app_secret: str):
    from . import db
    row = await db.query_one(
        "SELECT * FROM app_account WHERE app_id=%s AND enabled=1", (app_id,))
    if row and hmac.compare_digest(str(row["app_secret"]), str(app_secret)):
        return row
    return None


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def is_private_ip(ip: str) -> bool:
    """内网/回环/链路本地。给 /metrics 用：指标只对内网开放。

    走 ipaddress 解析而不是字符串前缀 —— "10." 这种前缀会把 100.x 也放进来。
    """
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def check_ip_whitelist(account: dict, ip: str):
    """按白名单判断来源 IP。

    原来只有一句 `ip.startswith(rule)` —— 白名单里写 `127.0.0.1` 会连带放行
    `127.0.0.1`、`127.0.0.1`，等于把整个同前缀网段开了。安全自检里一直是
    WARN，因为没人知道业务侧实际会怎么写规则，就记着没动。

    现在按规则形态分流，语义与运维直觉一致：
        `127.0.0.1`        单个 IP，只放行这一个
        `192.168.0.0/24`      CIDR 网段
        `10.6.6.`          末尾带点 = 前缀（老写法，保留兼容）
        `10.6.6.*`         通配（老写法，等价前缀）
    解析不了的规则按字符串全等处理 —— 宁可少放行，不能多放行。
    """
    white = (account.get("ip_whitelist") or "").strip()
    if not white:
        return
    allowed = [w.strip() for w in white.split(",") if w.strip()]
    try:
        addr = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        addr = None
    for rule in allowed:
        if rule == ip:
            return
        # 前缀写法（末尾点 / 星号）：只在前缀边界上匹配，"10.6.6." 不会吃到 "127.0.0.1"
        if rule.endswith(".*"):
            rule = rule[:-1]
        if rule.endswith("."):
            if ip.startswith(rule):
                return
            continue
        if "/" in rule:
            try:
                if addr is not None and addr in ipaddress.ip_network(rule, strict=False):
                    return
            except ValueError:
                continue
            continue
        # 纯 IP：精确相等
        if addr is not None:
            try:
                if addr == ipaddress.ip_address(rule):
                    return
            except ValueError:
                continue
        elif rule == ip:
            return
    raise HTTPException(status_code=403, detail="来源 IP 不在白名单内: %s" % ip)


def valid_emp_no(emp_no: str) -> bool:
    if not emp_no:
        return False
    return bool(re.match(config.EMP_NO_PATTERN, emp_no))


def verify_signature(request: Request, app_secret: str, body: bytes,
                     timestamp: str, nonce: str, signature: str):
    """可选的请求签名校验: HMAC(appSecret, method+path+timestamp+nonce+sha256(body))"""
    if not signature:
        if config.STRICT_SIGN:
            raise HTTPException(status_code=401, detail="缺少签名")
        return
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="时间戳非法")
    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=401, detail="时间戳超出允许偏差(5 分钟)")
    raw = "%s%s%s%s%s" % (request.method.upper(), request.url.path,
                          timestamp, nonce, hashlib.sha256(body).hexdigest())
    expect = hmac.new(app_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, signature):
        raise HTTPException(status_code=401, detail="签名校验失败")


async def get_app_account(app_id: str = Header(default="", alias="X-App-Id"),
                          authorization: str = Header(default="")) -> dict:
    """依赖: 解析并校验调用方身份。"""
    from . import db
    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:]
    if not app_id:
        raise HTTPException(status_code=401, detail="缺少 X-App-Id 请求头")
    body = parse_token(token)
    if body.get("app") != app_id:
        raise HTTPException(status_code=401, detail="令牌与 appId 不匹配")
    row = await db.query_one(
        "SELECT * FROM app_account WHERE app_id=%s AND enabled=1", (app_id,))
    if not row:
        raise HTTPException(status_code=401, detail="未知或未启用的调用方: %s" % app_id)
    return row


def make_admin_session() -> str:
    """管理员登录会话，默认 3 天有效（ADMIN_SESSION_TTL）。

    不直接拿 ADMIN_TOKEN 当会话用：那个是长期口令，没法过期；
    换成一个带 exp 的签名票据，到期后看板会要求重新登录。
    """
    body = {"typ": "admin", "role": "admin",
            "exp": int(time.time()) + config.ADMIN_SESSION_TTL,
            "iat": int(time.time())}
    payload = _b64e(json.dumps(body, separators=(",", ":")).encode())
    return payload + "." + _sign(payload)


def is_admin_token(value: str) -> bool:
    """X-Admin-Token 可以是两种：长期口令 ADMIN_TOKEN，或登录换来的 3 天会话票据。

    长期口令留给脚本/curl 用；看板只保存会话票据，所以过 3 天必然失效。
    """
    if not value:
        return False
    if hmac.compare_digest(value, config.ADMIN_TOKEN):
        return True
    try:
        body = parse_token(value, expect="admin")
    except HTTPException:
        return False
    return body.get("role") == "admin"


async def require_admin(x_admin_token: str = Header(default="")):
    if not is_admin_token(x_admin_token):
        raise HTTPException(status_code=403,
                            detail="管理员未登录或登录已过期（有效期 3 天），请重新登录")
    return True


async def get_viewer(app_id: str = Header(default="", alias="X-App-Id"),
                     authorization: str = Header(default=""),
                     x_admin_token: str = Header(default="")) -> dict:
    """看板数据的读取者。

    - 管理员令牌（长期口令或 3 天会话）→ 全站，可指定任意 appId
    - 接入系统（appId + Bearer）→ 只能看自己系统的数据
    - **什么都不带** → 访客：看板对内部开放，只读全站统计，不能改账号
      （管理接口走 require_admin，与这里无关，访客进不去）
    """
    if is_admin_token(x_admin_token):
        return {"app_id": "_all", "app_name": "管理员", "admin": True,
                "scope": "_all"}
    if not app_id and not authorization:
        # 访客能看全站看板（scope=_all），但**不是管理员**：
        # 以前这里给 admin=True，只读接口和"能改账号"的接口用同一个标志区分，
        # 迟早有人把 admin 当授权依据。现在拆成两个字段。
        return {"app_id": "_all", "app_name": "访客", "admin": False,
                "scope": "_all", "anonymous": True}
    account = await get_app_account(app_id, authorization)
    account["admin"] = False
    account["scope"] = account.get("app_id") or "_all"
    return account


def can_all(account: dict) -> bool:
    """能否看全站数据（管理员或访客）。

    与 admin 的区别：admin 才能改账号/配置；看全站统计只需要这个。
    """
    return bool(account.get("admin") or account.get("scope") == "_all")


def viewer_scope(account: dict, want: str = "") -> str:
    """把请求方希望的 appId 收敛成实际可查范围。

    管理员: 传了就查那个系统，没传查全局 '_all'。
    普通系统: 一律强制成自己，忽略传入值。
    """
    if can_all(account):
        return want or "_all"
    return account.get("app_id") or "_all"
