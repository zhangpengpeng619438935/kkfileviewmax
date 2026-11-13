import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import zlib
import time
import uuid
from datetime import datetime
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (alerts, collector, config, db, engine, loadmetric, metrics,
               ratelimit, security)
from .security import get_app_account, require_admin
from .stats_api import router as stats_router
from starlette.middleware.gzip import GZipMiddleware

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gateway")

app = FastAPI(title="kkFileView 预览服务网关", version="1.0.0",
              description="带工号鉴权、限流、统计与审计的文件预览 API 网关")


# ---- 错误原因记录：交给异常处理器，别指望中间件读响应体 ----
# FastAPI 有自己的一层 ExceptionMiddleware，在用户中间件**内侧**：
# 路由/依赖抛的 HTTPException、参数校验失败，都在那里就被转成响应了。
# 用户中间件（@app.middleware）拿到的是 BaseHTTPMiddleware 包装过的流式响应，
# 只有 body_iterator 没有 body —— 读 response.body 永远是空，落库就成了
# "HTTP 422（无响应体）"，看板上一排 4xx 看不出原因（实测 169 条）。
# 所以在异常**还没被转换**的时候就把原因写进 request.state.ev。
# 响应体仍交给 FastAPI 默认处理器，格式（{"detail": ...}）一个字节都不动 ——
# 已有调用方是按那个格式解析的，这里只做"顺手记一笔"。
from fastapi.exception_handlers import (                        # noqa: E402
    http_exception_handler, request_validation_exception_handler)
from fastapi.exceptions import RequestValidationError            # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTP   # noqa: E402


def _remember_error(request, msg: str):
    try:
        ev = getattr(request.state, "ev", None)
        if ev is not None and not ev.get("error_msg"):
            ev["error_msg"] = (msg or "")[:250]
    except Exception:
        pass


@app.exception_handler(StarletteHTTP)
async def _exc_handler(request, exc):
    _remember_error(request, str(getattr(exc, "detail", "") or ""))
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def _validation_handler(request, exc):
    _remember_error(request, _validation_text(exc))
    return await request_validation_exception_handler(request, exc)

# 响应压缩: 表格页 HTML(含数据 JSON)与统计接口响应可压缩到原来的 1/5 左右, 显著加快首屏
class _SelectiveGZip:
    """只对文本类响应启用 gzip, 文件流原样透传。

    为什么不能一刀切压缩:
      - PDF/图片/Office 产物本身已压缩, 再压收益极低, 还多耗 CPU 与内存;
      - 更关键的是压缩会改变长度, 而 **pdf.js 依赖 content-length 判断文件大小**,
        拿不到就报 "The PDF file is empty, i.e. its size is zero bytes"。
    所以 /kk/getCorsFile 和各类二进制产物直接透传, 保留引擎给的原始长度。
    """

    _RAW_PREFIX = ("/kk/getCorsFile",)
    _RAW_SUFFIX = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
                   ".tif", ".tiff", ".ico", ".mp4", ".webm", ".mp3",
                   ".wav", ".zip", ".rar", ".7z", ".tar", ".xlsx", ".docx",
                   ".pptx", ".xls", ".doc", ".ppt")
    # 唯一例外: CAD 转出来的 SVG。它是纯 XML 文本，压缩比通常 5~10 倍，
    # 而一份"布局平面图"实测能到 191MB —— 不压缩的话光传就几十秒。
    # （SVG 由浏览器按图片/文档渲染，不依赖 content-length，可以安全压缩）
    _TEXT_RAW = (".svg",)

    def __init__(self, app):
        self._app = app
        self._gz = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self._app(scope, receive, send)
        path = scope.get("path", "")
        low = path.lower()
        if (path.startswith(self._RAW_PREFIX)
                or (path.startswith("/kk/") and low.endswith(self._RAW_SUFFIX)
                    and not low.endswith(self._TEXT_RAW))):
            return await self._app(scope, receive, send)      # 文件流: 不压缩
        return await self._gz(scope, receive, send)           # 页面/JS/CSS/JSON: 压缩


app.add_middleware(_SelectiveGZip)


# ------------------------------------------------------------------ 生命周期
@app.on_event("startup")
async def on_start():
    # 挂载进来的 Windows 中文字体需要重建 fontconfig 缓存, 否则 LibreOffice
    # 仍然只认文泉驿, 转出来的 PDF 字形发虚、排版和原文差异大
    if os.path.isdir("/usr/share/fonts/win"):
        try:
            import subprocess
            subprocess.Popen(["fc-cache", "-f"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception as exc:
            log.warning("字体缓存刷新失败(不影响启动): %s", exc)
    await db.init_db()
    # 网关重启会打断正在跑的转换任务（进程被杀，来不及把状态写回去）。
    # 不清的话它们会永远停在 converting：/health 的队列数虚高，
    # 同名文件再打开时 claimed_task 还会把这个死任务返回给用户，一直等。
    # 实测线上残留过 4 条，最久的挂了 12 天。
    try:
        await db.execute(
            "UPDATE preview_task SET status='failed', err_msg='网关重启，转换中断',"
            " updated_at=NOW() WHERE status IN ('queued','converting')")
    except Exception as exc:
        log.warning("清理遗留转换任务失败(不影响启动): %s", exc)
    await collector.start()
    # 服务告警：只报容量/压力（限流风暴、排队超时、CPU/内存/磁盘、转换异常）。
    # 两个 worker 都会起，但内部用 Redis 选举 leader，只有 leader 落库。
    await alerts.start()
    # 预热重模块。openpyxl / pypdfium2 的首次 import 与首次建对象有一次性的
    # 0.6~0.7 秒开销（实测同一 xlsx：首次 0.97s、第二次 0.29s），
    # 在这里付掉，别让第一个打开表格的人白等。
    try:
        import io as _io

        import openpyxl
        import pypdfium2  # noqa: F401
        from . import office_pdf, univer_data, xlsx_html  # noqa: F401
        _wb = openpyxl.Workbook()
        _wb.active["A1"] = 1
        _buf = _io.BytesIO()
        _wb.save(_buf)
        univer_data.xlsx_to_workbook(_buf.getvalue(), "warm.xlsx")
    except Exception as exc:
        log.warning("预热失败(不影响启动): %s", exc)
    log.info("网关已启动: %s:%s  引擎=%s", config.HOST, config.PORT, config.KK_BASE)


@app.on_event("shutdown")
async def on_stop():
    await alerts.stop()
    await collector.stop()
    await db.close_db()


# ------------------------------------------------------------------ 埋点中间件
# 注意: 这里**不能**剔除 content-length —— pdf.js 靠它判断文件大小,
# 拿不到会直接报 "The PDF file is empty, i.e. its size is zero bytes"。
# 被 gzip 压缩的响应由 GZipResponder 自己重算长度, 不受影响(见 _SelectiveGZip)。
SKIP_HEADERS = {"transfer-encoding", "connection", "content-encoding",
                "keep-alive", "proxy-authenticate",
                "proxy-authorization", "te", "trailers", "upgrade"}


@app.middleware("http")
async def no_cache_page(request: Request, call_next):
    """看板页面与脚本不缓存(保证前端更新即生效); 构建产物长期缓存。

    /univer/assets/ 是 Vite 构建产物，文件名里带内容哈希（index-DNtlwfYD.js），
    内容一变文件名就变，天然不需要协商缓存。但这里原来只给了 /vendor/ 规则，
    Univer 那 6.3MB（gzip 后 1.7MB）每次打开表格/PPT 都重新下载一遍 ——
    内网 200ms、外网就得几秒，而它本来可以一次下载用到版本更新。
    """
    response = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html", "/app.js", "/style.css"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.startswith("/vendor/"):
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    elif path.startswith("/univer/assets/"):
        # 文件名带哈希：内容改了 URL 就变了，可以放心 immutable
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return response


# 限流拒绝（429）默认不写访问明细：限流风暴时拒绝量可达正常量几十倍，
# 逐条落库会压垮存储（见 collector.record 的说明）。但看板的"告警记录"要能看到
# 限流何时开始，所以这里按「每分钟 × 每个系统」采样一条——最坏 1440 条/天/系统，
# 既能定位问题，又不会被放大。
_RL_SAMPLE: dict = {}


def _allow_429_sample(key: str, window: float = 60.0) -> bool:
    now = time.time()
    last = _RL_SAMPLE.get(key)
    if last and now - last < window:
        return False
    if len(_RL_SAMPLE) > 500:
        _RL_SAMPLE.clear()
    _RL_SAMPLE[key] = now
    return True


@app.middleware("http")
async def static_cache_middleware(request: Request, call_next):
    """给前端静态资源加长缓存。

    three.min.js 603KB，原来没有任何 Cache-Control，每次打开 3D 页面都重下一遍
    （内网无所谓，走 RAG 那边从远处访问就明显了）。这些是构建产物，按周缓存即可，
    真要更新换文件名或清缓存。
    """
    response = await call_next(request)
    p = request.url.path
    if p.startswith("/three/") or p.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=604800"
    return response


def _error_detail_of(response) -> str:
    """从已经生成的错误响应里取出可读原因（尽量）。

    **注意**：这个中间件拿到的响应是 BaseHTTPMiddleware 包装过的流式对象，
    只有 body_iterator、没有 body —— 直接读 getattr(response, "body") 永远是空，
    所以以前写出去的是"HTTP 422（无响应体）"。
    真正可靠的原因是下面 _exc_handler / _validation_handler 直接写进
    request.state.ev 的；这里只作为兜底（万一有非 FastAPI 抛出的错误响应）。
    """
    try:
        body = getattr(response, "body", b"") or b""
        if not body:
            return "HTTP %s" % getattr(response, "status_code", 0)
        data = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        return "HTTP %s" % getattr(response, "status_code", 0)
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str):
        return detail
    msg = data.get("msg") if isinstance(data, dict) else None
    return str(msg or ("HTTP %s" % getattr(response, "status_code", 0)))


def _validation_text(exc) -> str:
    """把 FastAPI 的参数校验错误压成一句人看懂的话。"""
    bits = []
    try:
        for it in (exc.errors() or [])[:4]:
            loc = ".".join(str(x) for x in (it.get("loc") or []) if x != "body")
            bits.append("%s %s" % (loc or "参数", it.get("msg") or "不合法"))
    except Exception:
        return "参数校验失败"
    return "参数校验失败: " + "；".join(bits) if bits else "参数校验失败"


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """请求级指标与落库。

    **这个装饰器必须紧贴在 def 上方**：@app.middleware("http") 是按"下一个
    def"注册的，中间插一个工具函数就会把它注册成中间件（实测启动即 500：
    _error_detail_of() takes 1 positional argument but 2 were given）。
    """
    start = time.perf_counter()
    ev = {
        "request_id": uuid.uuid4().hex,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "client_ip": security.client_ip(request),
        "user_agent": request.headers.get("user-agent", "")[:250],
        "action": "api",
    }
    request.state.ev = ev
    request.state.app_id = ""
    try:
        response = await call_next(request)
    except HTTPException as exc:
        ev.update({"status_code": exc.status_code, "success": False,
                   "error_msg": str(exc.detail)[:250]})
        response = JSONResponse(status_code=exc.status_code,
                                content={"code": exc.status_code,
                                         "msg": str(exc.detail), "data": None})
    except Exception as exc:
        log.exception("请求处理异常: %s %s", request.method, request.url.path)
        ev.update({"status_code": 500, "success": False, "error_msg": str(exc)[:250]})
        response = JSONResponse(status_code=500,
                                content={"code": 500, "msg": "服务内部错误", "data": None})
    ev["cost_ms"] = int((time.perf_counter() - start) * 1000)
    ev.setdefault("status_code", response.status_code)
    ev.setdefault("success", response.status_code < 400)
    ev["app_id"] = getattr(request.state, "app_id", "") or ""
    # 补全错误原因。
    # 上面只接住了"从 call_next 抛出来"的异常，但 FastAPI 自己有一层
    # ExceptionMiddleware 在用户中间件**内侧**：路由/依赖/参数校验抛的
    # HTTPException 与 RequestValidationError 在那里就被转成响应了，
    # 中间件只看到一个 4xx，error_msg 是空的。实测 169 条失败记录没有任何原因，
    # 面板上就是一排"400 / 401"看不出为什么 —— 排查时只能靠重放请求。
    if response.status_code >= 400 and not ev.get("error_msg"):
        ev["error_msg"] = _error_detail_of(response)[:250]
    path = request.url.path
    # 运行指标（/metrics 输出）：路径先归组，否则每个 fileKey 都是一条时间序列
    if not path.startswith("/health"):
        metrics.observe(metrics.group_of(path), response.status_code, ev["cost_ms"])
    recorded = False
    if response.status_code == 429:
        who = ev["app_id"] or (request.headers.get("x-app-id", "") or
                               security.client_ip(request))
        if _allow_429_sample(who):
            ev["app_id"] = who
            ev["error_msg"] = ev.get("error_msg") or "限流：该分钟首次被拒"
            ev["sampled"] = True          # 告诉 collector 这是一条采样记录，别按 429 丢掉
            collector.record(ev)
            recorded = True
    if path.startswith("/health"):
        pass                                   # 健康检查不计入统计
    elif path.startswith("/sheet/"):
        collector.record(ev)                   # 表格模式打开预览页
    elif path.startswith("/kk/"):
        # 预览页与文件流计入; 页面里的 js/css/图片等静态资源不计入, 否则一次预览会被放大成十几次
        first = path[4:].split("/")[0]
        if first in PROXY_GUARD or first in PROXY_FILE:
            collector.record(ev)
    elif path.startswith("/api/"):
        # 只记录"真正的业务调用"。看板自己的统计查询、换令牌、管理接口、
        # 以及前端的转码状态轮询都不能算 —— 否则刷新一次看板就凭空多出几十次访问，
        # 实测曾把"空工号"刷到 2000+ 次、把管理员身份写成 app_id=_all。
        if not recorded and path in ("/api/v1/preview/url",
                                     "/api/v1/preview/warm",
                                     "/api/v1/preview/page"):
            collector.record(ev)
    response.headers["X-Request-Id"] = ev["request_id"]
    return response


def ok(data=None, msg="ok"):
    return {"code": 0, "msg": msg, "data": data}


# ------------------------------------------------------------------ 认证
class TokenIn(BaseModel):
    appId: str
    appSecret: str


@app.post("/api/v1/auth/token")
async def auth_token(body: TokenIn, request: Request):
    row = await security._load_account(body.appId, body.appSecret)
    if not row:
        raise HTTPException(status_code=401, detail="appId 或 appSecret 不正确")
    security.check_ip_whitelist(row, security.client_ip(request))
    request.state.app_id = body.appId
    return ok({"accessToken": security.make_token(body.appId),
               "expiresIn": config.TOKEN_TTL,
               "qpsLimit": row.get("qps_limit", config.DEFAULT_APP_QPS)})


class AdminLoginIn(BaseModel):
    token: str = ""


@app.post("/api/v1/admin/login")
async def admin_login(body: AdminLoginIn):
    """管理员登录：拿长期口令 ADMIN_TOKEN 换一张 3 天有效的会话票据。

    看板只保存这张票据，到期（ADMIN_SESSION_TTL）后所有管理接口返回 403，
    页面提示重新登录。ADMIN_TOKEN 本身不进浏览器 localStorage。
    """
    token = (body.token or "").strip()
    if not token or not hmac.compare_digest(token, config.ADMIN_TOKEN):
        await asyncio.sleep(0.5)          # 轻微延时，削弱暴力猜测
        raise HTTPException(status_code=403, detail="管理员令牌不正确")
    ttl = config.ADMIN_SESSION_TTL
    return ok({"adminSession": security.make_admin_session(),
               "expiresIn": ttl,
               "expiresAt": int(time.time()) + ttl,
               "expiresAtText": datetime.fromtimestamp(
                   time.time() + ttl).strftime("%Y-%m-%d %H:%M")})


# ------------------------------------------------------------------ 预览
class PreviewIn(BaseModel):
    fileUrl: str
    empNo: str
    empName: str = ""
    dept: str = ""
    fileName: str = ""
    watermark: str = ""
    wait: bool | None = None          # 是否同步等待转码完成(缺省按服务端配置)
    waitSeconds: int = 0              # 最多等几秒(0=按服务端配置)


def _plain_err(msg: str) -> str:
    """引擎的报错常带 <p style="color:red"> 这类 HTML, 转成纯文本再返回给调用方。"""
    return re.sub(r"<[^>]+>", "", msg or "").strip()[:200]


async def _source_encryption(file_url: str, ext: str) -> str:
    """只读源文件前 80 字节，判断是否被企业文档加密系统包裹。

    这些文件（首字节 `%TSD-Header-###%`）在装了加密客户端的 PC 上打开是透明的，
    服务器侧却只能拿到密文——LibreOffice 会先"研究"十几秒再报错。
    一次 80 字节的 Range 请求就能提前定性，代价 ~100ms。
    源站忽略 Range / 报错时返回空串，交给转换流程兜底判定。

    **不按扩展名限制**：最早只查 Office，结果 3D 模型被漏掉——
    `CA_C318_ARHUD_3.1INCH_V2.0_20241220.stp`（484KB）是 TSD 密文，
    网关放行给引擎的 O3DV，浏览器解析后拿到空模型，报
    "Failed to import model. The model doesn't contain any meshes."，
    而且白等 56 秒。加密和扩展名无关，任何类型都要看头 80 字节。
    """
    from . import office_pdf
    ext = (ext or "").lower()
    # probe_file 刚用 Range 取过前 80 字节（2 分钟内留在这条缓存里），直接复用：
    # 首次预览原来对同一个文件发两条 80 字节请求。
    try:
        r = await db.get_redis()
        raw = await r.get(engine._probe_head_key(file_url))
        if raw:
            return office_pdf.encryption_of(base64.b64decode(raw)[:80], ext)
    except Exception:
        pass
    try:
        client = await engine.http()
        buf = b""
        async with client.stream("GET", file_url,
                                 headers={"Range": "bytes=0-79"},
                                 timeout=6) as r:
            if r.status_code >= 400:
                return ""
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= 80:
                    break
        return office_pdf.encryption_of(buf[:80], ext)
    except Exception:
        return ""


# asyncio.create_task 的返回值必须有人持有：官方文档写明不保存引用时，任务可能在
# 执行途中被回收（尤其转换里大量 await）。被回收后 preview_task 就永久停在 queued。
_BG_TASKS: set = set()


def _spawn_convert(fkey: str, file_url: str, task_id: str, fname: str):
    t = asyncio.create_task(
        engine.convert_task(fkey, file_url, task_id, fname,
                            engine.to_engine_url(file_url)))
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)
    return t


async def _start_convert(fkey: str, file_url: str, fname: str,
                         app_id: str, emp_no: str) -> str:
    """起一个后台转换任务(同一文件并发时只允许一个), 返回 task_id。

    抽成函数是因为除了 /preview/url，还有"缓存过期后重新打开预览页"这条路
    也要能重新起任务（见 preview_page / pdfview / sheet 的失效分支）。
    """
    task_id = await engine.claimed_task(fkey)
    if task_id and not await engine.task_alive(task_id):
        task_id = ""
    if not task_id:
        # 锁可能已过期（进程重启、或转换比锁的 TTL 还长），但任务其实还在跑。
        # 直接开新任务会叠出两路 LibreOffice、产物互相覆盖 —— 所以先查表。
        task_id = await engine.active_task(fkey)
        if task_id:
            await engine.task_claim(fkey, task_id)   # 顺手把锁补回去
            return task_id
        candidate = uuid.uuid4().hex
        if await engine.task_claim(fkey, candidate):
            task_id = candidate
            await db.execute(
                "INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url, file_name, status)"
                " VALUES (%s,%s,%s,%s,%s,%s,'queued')",
                (task_id, app_id, emp_no, fkey, file_url, fname[:500]))
            _spawn_convert(fkey, file_url, task_id, fname)
        else:
            # 抢不到说明别人正在转：等它把 id 写进去再取。
            # 拿不到就返回空 —— **绝不能自己起任务**：没有锁的情况下转换，
            # 同一文件会叠出两三路 LibreOffice，产物互相覆盖。
            task_id = await engine.claimed_task(fkey)
            if not task_id:
                await asyncio.sleep(0.1)
                task_id = await engine.claimed_task(fkey)
    return task_id


async def _refresh_step(fkey: str, file_url: str) -> None:
    """STEP 源文件被覆盖后，排队重建网格 —— **不删旧网格**。

    通用过期流程是 `invalidate()` 删产物再重转，对网格代价太大：47MB 的装配体
    重建要 20 秒以上，删掉之后用户连旧模型都看不到（页面只剩"正在首次解析"）。
    网格本来就是原子替换写出来的（step_one.write_gz_atomic 先写 .part 再 rename），
    所以只排队就行：重建期间继续用旧网格，完成后下一次打开就是新模型。

    判据用 mtime 而不是 kk:srcfp：网格的指纹由离线 preconvert_step 维护，
    按需队列重建（step_one）不写指纹，拿指纹判会变成"每 10 分钟重排一次"。
    mtime 自限 —— 重建出来的网格必然比源文件新，下次打开就不会再排。
    """
    from . import step_view
    art = ""
    for p in (step_view.mesh_path(fkey), step_view.empty_path(fkey),
              step_view.nomesh_path(fkey)):
        if os.path.exists(p):
            art = p
            break
    if not art:
        return                        # 还没产物：正常流程会入队，不用在这插一脚
    lp = engine.local_path_of(file_url)
    if not lp or not os.path.exists(lp):
        return                        # 取不到本地副本（别的文件服务器），判断不了
    try:
        if os.path.getmtime(lp) <= os.path.getmtime(art) + 1:
            return                    # 网格不比源文件旧
    except OSError:
        return
    log.info("STEP 源文件已更新，排队重建网格(%s)，旧网格继续可用", fkey[:12])
    await _step_enqueue(fkey, file_url)


async def _refresh_if_expired(fkey: str, file_url: str, app_id: str,
                              emp_no: str, start: bool = True) -> bool:
    """缓存超过有效期就删产物（可选：立刻重新起转换任务），返回 True 表示已作废。

    只在"有产物"的类型上会命中（Office/表格/SVG 等网关自己转的）；
    PDF/图片这类直接读源站的没有产物，本来就是最新内容。

    start=False 给 /preview/url 用：那里后面本来就会建任务，
    这里再建一次会在"极快文件"（如 xlsx 0.1 秒转完）时重复转一遍。
    """
    # STEP 单独一条：网格走"后台重建 + 原子替换"，不能删了重转（见 _refresh_step）
    if engine.ext_of(file_url) in config.STEP_EXTS:
        await _refresh_step(fkey, file_url)
        return False
    # 产物路径只解析一次：过期判定和变更判定都要它（7 个 os.path.exists），传下去省一轮
    art = engine.artifact_path(fkey)
    expired = await engine.cache_expired(fkey, art)
    if not expired:
        # 没过期也要看一眼源文件变没变：受控文件常被"原地覆盖"（同名同路径，
        # 只是内容换了），如果非要等 TTL 到点才比指纹，用户会在一段时间里
        # 一直看到旧内容 —— 而 stat 一次只要几微秒。
        # 只有"本地有副本"的文件会走到这里（纯 HTTP 源仍按 TTL，不主动 HEAD）。
        if not await engine.changed_locally(fkey, file_url, art):
            return False
    else:
        # 过期 ≠ 内容变了。先 HEAD 比一下源站指纹，没变就续期，别白转一遍
        # （CAD 一次要 19~30 秒，用户体感就是"怎么又要等 20 秒"）。
        if await engine.revalidate(fkey, file_url):
            return False
    # 判断不了"源变没变"（签名过期、源站抽风、本地文件也读不到）时**不许删缓存**：
    # 删掉之后要重新拿源转换，源既然不可达，用户就彻底打不开了 ——
    # 旧产物总比报 403 强。等源恢复后自然会被下一次校验判定。
    if not await engine.source_reachable(file_url):
        log.warning("源暂不可达，保留旧缓存(%s)，不做重转", fkey)
        return False
    await engine.invalidate(
        fkey, ("缓存超过 %d 分钟" % config.CACHE_TTL_MINUTES) if expired
        else "源文件已更新")
    if start:
        await _start_convert(fkey, file_url, engine.name_of(file_url),
                             app_id or "_all", emp_no or "")
    return True


async def _cad_migrate(fk: str, file_url: str) -> bool:
    """把"引擎已转好、但标记还是 pdf"的 CAD 迁到位图通道。

    老任务（CAD 位图模式上线前）在 Redis 里留下的是 `pdf`，页面上就按引擎的
    默认方式走 pdf.js。那份 PDF 一条线段一个描边块，复杂图纸 pdf.js 渲染
    4 分钟都出不来（实测 246 秒仍白屏），而同一份 PDF 用 PDFium 出整页图只要
    0.8 秒。所以这里把引擎的 PDF 拉到本地，改标 `cad`，用户立即变成秒开。

    拉取失败（源站签名到期/引擎丢产物）时给 5 分钟冷却，别让每次打开都白等一次。
    """
    from . import cad_view
    if cad_view.pdf_ready(fk):
        return True
    try:
        r = await db.get_redis()
        if await r.get("kk:cadmig:" + fk):
            return False
        client = await engine.http()
        resp = await client.get(
            engine.engine_preview_url(engine.to_engine_url(file_url)),
            timeout=config.CONVERT_TIMEOUT)
        pdf_url = cad_view.pdf_url_of_page(resp.text) if resp.status_code < 400 else ""
        if pdf_url and await cad_view.cache_pdf(pdf_url, fk):
            await engine.mark_ready(fk, mode="cad")
            log.info("CAD 已从 pdf.js 迁移到位图模式: %s", fk)
            return True
        await r.set("kk:cadmig:" + fk, "1", ex=300)
    except Exception as exc:
        log.warning("CAD 迁移位图失败(%s): %s", fk, exc)
    return False


@app.post("/api/v1/preview/url")
async def preview_url(body: PreviewIn, request: Request,
                      account: dict = Depends(get_app_account)):
    app_id = account["app_id"]
    request.state.app_id = app_id
    await ratelimit.check(app_id, body.empNo, account.get("qps_limit"))

    if not security.valid_emp_no(body.empNo):
        raise HTTPException(status_code=400, detail="工号格式不合法: %s" % body.empNo)

    file_url = engine.normalize_file_url(body.fileUrl)   # 修正未转义的 & / 空格
    url_fixed = file_url != body.fileUrl.strip()
    try:
        engine.check_source_allowed(file_url)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    fkey = engine.file_key_of(file_url)
    ext = engine.ext_of(file_url)
    fname = body.fileName or engine.name_of(file_url)
    ev = request.state.ev
    ev.update({"emp_no": body.empNo, "emp_name": body.empName, "dept": body.dept,
               "file_key": fkey, "file_name": fname, "file_ext": ext,
               "file_type": engine.file_type_of(ext), "action": "preview",
               "file_url": file_url, "audit": True})

    # 缓存有效期(默认 30 分钟): 过期就删产物 + 重新下载源文件 + 重新转换,
    # 保证用户看到的是最新内容(源站文件被替换后, 旧 PDF 不会一直顶着用)。
    # start=False: 后面的建任务分支会接手, 避免重复起任务。
    await _refresh_if_expired(fkey, file_url, app_id, body.empNo, start=False)
    ready = await engine.is_ready(fkey)
    rid = ev.get("request_id", "")
    token = security.make_preview_token(fkey, body.empNo, file_url, app_id,
                                        body.empName, body.dept, req_id=rid)
    preview_url = "%s/api/v1/preview/page?token=%s" % (config.PUBLIC_BASE, token)
    # 登记"申请时刻"：预览页在内容真正可见时会用 sendBeacon 报回来，
    # 这里的时间点减去它，就是用户实际等待的加载耗时（见 loadmetric.py）。
    await loadmetric.mark_request(rid, fkey, body.empNo, app_id, fname, file_url,
                                  cache_hit=ready)

    extra = {"fileUrl": file_url, "urlFixed": url_fixed}

    if ready:
        ev["cache_hit"] = True
        ev["result"] = "success"
        hit = {"status": "ready", "fileKey": fkey, "fileName": fname,
               "fileType": engine.file_type_of(ext),
               "previewUrl": preview_url, "expiresIn": config.PREVIEW_TOKEN_TTL, **extra}
        # 大 PPT 两阶段：此时 PDF 只有前 N 页，完整版还在后台转。
        # 不把进度告诉调用方的话，用户翻到第 N 页会以为文件被截断了。
        part = await engine.partial_of(fkey)
        if part:
            hit["partial"] = part
            cur, total = part.split("/", 1)
            hit["partialTip"] = ("高清排版转换中，当前可看前 %s 页（共 %s 页），"
                                 "完整版转好后页面会自动更新，无需手动刷新" % (cur, total))
        return ok(hit)

    # 近期转码失败过(如 LibreOffice 直接崩掉的文件): 不再重复试, 直接告知原因,
    # 免得用户每打开一次都要白等一轮等待。5 分钟后自动放开重试。
    recent_fail = await engine.recent_failure(fkey)
    if recent_fail and not recent_fail.startswith("ENC:"):
        # 早期版本还没有加密检测，会把密文文件报成"老版二进制格式 .pptx 无法文字降级"。
        # 这种旧记录还挂在 Redis 上时，补一次 80 字节探测，命中就以加密为准覆盖掉。
        _old_enc = await _source_encryption(file_url, ext)
        if _old_enc:
            recent_fail = "ENC:" + _old_enc
            await engine.mark_failed(fkey, recent_fail, ttl=1800)
    if recent_fail:
        ev["result"] = "error"
        ev["error_msg"] = recent_fail
        if recent_fail.startswith("ENC:"):
            # 文档加密系统包裹的文件：给出用户能理解的结论，不带技术术语。
            # Redis 里存的是系统名（ENC:TSD 文档加密），这里拼成完整的话。
            raise HTTPException(status_code=422,
                                detail=encrypted_msg(recent_fail[4:]))
        raise HTTPException(
            status_code=422,
            detail="该文件近期转码失败（已暂停重试，稍后自动放开）：%s" % recent_fail)

    # 首次预览先探测源站(超时不阻塞, 交给后台任务), 明确失败才在接口层报错。
    # 注意: 这一步对**所有**首次预览生效, 不能只放在"需要转码"的分支里——
    # PDF/图片这类免转码文件同样依赖源站, 源站给了 0 字节时若不拦,
    # 用户只会看到 pdf.js 的 "The PDF file is empty, i.e. its size is zero bytes"。
    src_ok, src_status, src_detail = await engine.probe_file(file_url)
    if not src_ok and src_status:
        detail_txt = src_detail or "HTTP %s" % src_status
        ev["result"] = "error"
        ev["error_msg"] = "文件地址不可访问: %s" % detail_txt
        raise HTTPException(
            status_code=400,
            detail="文件地址不可访问(%s)。请检查: 链接是否有效或已过期、是否需要鉴权、"
                   "文件名中的 & 空格等特殊字符是否已转义、源站文件是否为空。"
                   "服务端实际使用的地址: %s"
                   % (detail_txt, file_url[:220]))

    # 文档加密系统包裹的文件：下载前 80 字节就能定性。
    # 不拦的话会先排进转换队列，等 LibreOffice"研究"十几秒才失败
    # （实测 13MB 的加密 pptx 端到端 33.9 秒，最后仍是空白页）。
    # 走到这里说明还没就绪（缓存命中在前面已 return），探测成本只付一次。
    _enc = await _source_encryption(file_url, ext)
    if _enc:
        msg = encrypted_msg(_enc)
        await engine.mark_failed(fkey, "ENC:" + _enc, ttl=1800)
        ev["result"] = "error"
        ev["error_msg"] = msg
        raise HTTPException(status_code=422, detail=msg)

    need_convert = (ext in config.CONVERT_EXTS
                    or (config.SVG_PREVIEW and ext in config.SVG_EXTS)   # SVG 网关自己渲染
                    # 压缩包由网关自己列清单(几十毫秒), 也要走任务流程才会生成页面
                    or (config.ARCHIVE_RENDER and ext in config.ARCHIVE_EXTS))
    if not need_convert:
        await engine.mark_ready(fkey)   # PDF/图片/文本等无需转码, 直接放行
        ev["cache_hit"] = True
        ev["result"] = "success"
        return ok({"status": "ready", "fileKey": fkey, "fileName": fname,
                   "fileType": engine.file_type_of(ext),
                   "previewUrl": preview_url, "expiresIn": config.PREVIEW_TOKEN_TTL, **extra})

    # 同一文件并发请求只允许一个去建任务(跨 worker 用 Redis 抢占)
    task_id = await _start_convert(fkey, file_url, fname, app_id, body.empNo)
    ev["is_async"] = True
    # 同步等待(默认开启): 转码完成后直接返回预览地址, 调用方不必自己轮询
    # 上限 30 秒：waitSeconds 由调用方给，不封顶的话一个请求就能把 worker
    # 占住十分钟（每 0.15 秒一次 is_ready + 一次 DB 查询）。
    wait_limit = min(float(body.waitSeconds or config.PREVIEW_WAIT_SECONDS), 30.0)
    do_wait = config.PREVIEW_WAIT if body.wait is None else body.wait
    # PPT/PPTX 冷转要 20 秒以上(LibreOffice 单线程, 119 页实测 21 秒),
    # 同步等待没有意义: 立刻返回, 页面先给"快速内容版", 后台转好自动切换成 PDF。
    if do_wait and ext in ("ppt", "pptx"):
        do_wait = False
    if do_wait and wait_limit > 0:
        # 0.15 秒粒度轮询: 原来是 1 秒, 转码 2.2 秒时会白等到 3 秒才返回
        waited = 0.0
        while waited < wait_limit:
            await asyncio.sleep(0.15)
            waited += 0.15
            if await engine.is_ready(fkey):
                ev["is_async"] = True
                ev["result"] = "success"
                return ok({"status": "ready", "fileKey": fkey, "fileName": fname,
                           "fileType": engine.file_type_of(ext),
                           "previewUrl": preview_url,
                           "expiresIn": config.PREVIEW_TOKEN_TTL,
                           "waitedSeconds": round(waited, 2), **extra})
            # 任务已经失败就立刻返回: 否则会一直干等到 wait_limit 才回一个
            # 误导性的 "converting", 调用方看不出其实是转码失败
            st = await db.query_one(
                "SELECT status, err_msg FROM preview_task WHERE task_id=%s", (task_id,))
            if st and st["status"] == "failed":
                msg = _plain_err(st.get("err_msg")) or "未知原因"
                await engine.mark_failed(fkey, msg)
                ev["result"] = "error"
                ev["error_msg"] = msg
                tail = "" if msg.endswith(("。", ".", "！", "!", "？", "?")) else "。"
                raise HTTPException(
                    status_code=422,
                    detail="文件转码失败（耗时 %.1f 秒）：%s%s该文件可能含有转换引擎无法处理的元素，"
                           "请联系管理员；同一文件 5 分钟内不会重复重试。"
                           % (waited, msg, tail))

    # PPT 不等转码: 直接返回预览地址, 页面会先展示"快速内容版", PDF 就绪后自动切换
    if ext in ("ppt", "pptx"):
        if not await engine.is_ready(fkey):
            ev["is_async"] = True
            ev["result"] = "success"
            return ok({"status": "ready", "fileKey": fkey, "fileName": fname,
                       "fileType": engine.file_type_of(ext),
                       "previewUrl": preview_url, "expiresIn": config.PREVIEW_TOKEN_TTL,
                       "provisional": True, **extra})
        # 大 PPT 走了两阶段: 前 N 页已可看, 完整版还在后台转
        part = await engine.partial_of(fkey)
        if part:
            ev["is_async"] = True
            ev["result"] = "success"
            return ok({"status": "ready", "fileKey": fkey, "fileName": fname,
                       "fileType": engine.file_type_of(ext), "previewUrl": preview_url,
                       "expiresIn": config.PREVIEW_TOKEN_TTL,
                       "partial": part, "partialTip": "高清排版转换中，当前可看前 %s 页（共 %s 页），"
                                                      "完整版转好后页面会自动更新，无需手动刷新" % tuple(part.split("/", 1)),
                       **extra})

    ev["is_async"] = True
    ev["result"] = "success"
    return ok({"status": "converting", "fileKey": fkey, "fileName": fname,
               "taskId": task_id, **extra,
               # 必须带上 previewUrl：首次响应预算只有 2.5 秒，转码慢的格式（dwg、大 docx）
               # 会走到这里。调用方直接打开这个地址即可，页面自己显示"转换中"并在转好后
               # 自动切到内容，不需要先轮询再重新调用。
               "previewUrl": preview_url,
               "expiresIn": config.PREVIEW_TOKEN_TTL,
               "waitedSeconds": round(wait_limit, 2),
               "pollUrl": "%s/api/v1/preview/status?taskId=%s" % (config.PUBLIC_BASE, task_id),
               "tip": "转码超过首次响应预算（%s 秒）。可直接打开 previewUrl（页面会自动等待并切换），"
                      "也可以轮询 pollUrl" % wait_limit})


@app.get("/api/v1/preview/status")
async def preview_status(taskId: str, request: Request,
                         account: dict = Depends(get_app_account)):
    request.state.app_id = account["app_id"]
    row = await db.query_one("SELECT * FROM preview_task WHERE task_id=%s", (taskId,))
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    data = {"taskId": taskId, "status": row["status"], "fileName": row["file_name"],
            "errMsg": row["err_msg"]}
    if row["status"] == "ready":
        data["previewUrl"] = "%s/api/v1/preview/page?token=%s" % (
            config.PUBLIC_BASE,
            security.make_preview_token(row["file_key"], row["emp_no"],
                                        row["file_url"], row["app_id"]))
    return ok(data)


@app.get("/api/v1/preview/page")
async def preview_page(token: str, request: Request):
    """校验一次性预览令牌后跳转到引擎预览页(引擎不对外暴露)。"""
    info = security.parse_preview_token(token)
    ev = request.state.ev
    # 令牌里带 app_id, 这样浏览器打开预览页也能归属到调用系统
    app_id = info.get("app", "") or getattr(request.state, "app_id", "")
    request.state.app_id = app_id
    ev.update({"app_id": app_id,
               "emp_no": info.get("emp", ""),
               "emp_name": info.get("nm", ""),
               "dept": info.get("dp", ""),
               "file_key": info.get("fk", ""),
               "file_url": info.get("url", ""),
               "file_name": engine.name_of(info.get("url", "")),
               "file_ext": engine.ext_of(info.get("url", "")),
               # 打开预览页只进 access_log（访问量/延迟统计用），不再写审计:
               # 一次预览已经由 /preview/url 记了一条，这里再记就是重复。
               "action": "open", "result": "success"})
    # 令牌同时放进 URL(kkt) 与 Cookie:
    #  - URL 令牌: 供跨域 iframe 使用(第三方 Cookie 会被浏览器拦截, 不能只靠 Cookie)
    #  - Cookie  : 供同域直接打开的场景使用
    # 表格模式命中: 直接打开渲染好的表格页; 其余走引擎(地址需二次编码, 抵消引擎的 URL 规范化)
    from . import cad_view, sheet, step_view
    fk = info["fk"]
    # 文档加密系统的文件：先给结论，别让它进转换队列白等半分钟。
    # 先看近期失败缓存（转换任务已经判过），再自己探 80 字节快速判定。
    _fail = await engine.recent_failure(fk)
    if not _fail:
        _enc = await _source_encryption(info["url"], engine.ext_of(info["url"]))
        if _enc:
            _fail = "ENC:" + _enc
            await engine.mark_failed(fk, _fail, ttl=1800)
    if _fail.startswith("ENC:"):
        return HTMLResponse(_encrypted_page(_fail[4:]))
    engine_target = "/kk/onlinePreview?url=%s&kkt=%s" % (
        engine.encode_url(engine.to_engine_url(info["url"])), token)
    # 缓存超过有效期(默认 30 分钟): 作废产物并重新转换。
    # 直接打开旧链接/刷新页面的场景也要覆盖, 否则用户会一直看到 30 分钟前的版本。
    if await _refresh_if_expired(fk, info["url"], app_id, info.get("emp", "")):
        target = "/prog/%s?kkt=%s" % (fk, quote(token))   # 转换等待页, 就绪后自动跳回
        resp = RedirectResponse(url=target)
        resp.set_cookie("kk_pv", token, httponly=True, samesite="lax", path="/")
        return resp
    mode = await engine.ready_mode(fk)
    # Redis 标记可能被清过(运维清缓存/过期), 以磁盘产物或任务表兜底判断,
    # 否则会出现"已经转好了却还进内容页"的来回跳转。
    if mode == "" and sheet.local_pdf_ready(fk):
        mode = "pdf_local"
    _ext0 = engine.ext_of(info["url"])
    # CAD 的老标记是 pdf（位图模式上线前转的）。引擎已经转好了 PDF，只是一直用
    # pdf.js 打开——复杂图纸要几分钟还白屏。这里就地迁到位图通道，别让用户继续等。
    if (config.CAD_RASTER and _ext0 in config.CAD_EXTS and mode != "cad"
            and await engine.is_ready(fk) and await _cad_migrate(fk, info["url"])):
        mode = "cad"
    if _ext0 in config.STEP_EXTS and (step_view.mesh_ready(fk)
                                      or step_view.wire_ready(fk)):
        # 3D 实体：网格是离线预解析好的，页面只负责下载 + 画（1~2 秒）。
        # 只有线框、还没有网格时，补一次入队 —— 否则如果 mesh 阶段当时没跑到
        # （worker 被大文件堵住、或手动清了产物），用户会永远停在"实体解析中"，
        # 页面还每隔 4 秒问一次状态，看着像在转其实没人在转。
        # _step_enqueue 有 SETNX 10 分钟去重，多人反复打开也只排一次。
        if not step_view.mesh_ready(fk):
            await _step_enqueue(fk, info.get("url", ""))
        target = "/stepview/%s?kkt=%s" % (fk, quote(token))
    elif _ext0 in config.STEP_EXTS and step_view.empty_reason(fk):
        # 预解析过、但模型里没有实体面。放行到 O3DV 只会白解析几十秒再报
        # "doesn't contain any meshes"（实测 56 秒），直接给结论。
        target = "/stepempty/%s?kkt=%s" % (fk, quote(token))
    elif _ext0 in config.STEP_EXTS:
        # 剩下的 STEP 都是"没网格也没线框"：预转换只扫受控文件目录，
        # 散落在别的目录（治具图纸等）的 STEP 永远不会被预转。以前这里直接
        # 落到引擎，让浏览器用 WASM 现场解析 —— 17MB 要二十多秒、200MB 基本
        # 打不开，用户看到的就是一直白屏。改成入队让宿主机补网格，
        # 页面给"正在首次解析"，转好自动跳 /stepview。
        await _step_enqueue(fk, info.get("url", ""))
        target = "/stepwait/%s?kkt=%s" % (fk, quote(token))
    elif (config.UNIVER_PREVIEW and _ext0 in config.UNIVER_SLIDE_EXTS
            and sheet.local_pdf_ready(fk)):
        # 幻灯片走内置 Univer Slides：每页一张位图，翻页/缩放/全屏都由 Univer 管。
        # 必须等 PDF 就绪再进来——否则 Univer 页面只能空转（它拿不到页数据）。
        target = "/univer/%s?kkt=%s" % (fk, quote(token))
    elif (config.UNIVER_PREVIEW and _ext0 in config.UNIVER_SHEET_EXTS
          and (sheet.sheet_ready(fk) or sheet.local_pdf_ready(fk)
               or sheet.snap_ready(fk))
          and not await engine.nosnap(fk)):
        # 表格走内置 Univer（可缩放、看公式、切工作表）。
        # 必须等产物就绪再进来：没就绪时进 Univer 只是一个空壳，还要白加载 6MB JS；
        # 走 /prog 等待页既能看到进度，转好后（可能是大表转出的 PDF）也不会绕这一圈。
        # nosnap：这个文件的快照生成不了（文件残缺、openpyxl 读不出工作表等）。
        # Univer 只认快照，进来就是白屏干等 —— 退到下面的静态表格页，至少能看能滚。
        target = "/univer/%s?kkt=%s" % (fk, quote(token))
    elif mode == "" and (_ext0 in PROG_EXTS or _ext0 in config.CONVERT_EXTS) \
            and not await engine.is_ready(fk):
        # 还没转好: PPT 先给秒开的内容页, 其余给"转换中"等待页;
        # 两者都会自己轮询, 就绪后自动跳到正式查看器。
        # 这里必须覆盖全部 CONVERT_EXTS —— 否则 DWG 这类"引擎页要同步转换 12 秒"的文件
        # 会直接进引擎页, 用户对着白屏干等。
        target = "/prog/%s?kkt=%s" % (fk, quote(token))
    elif mode == "cad":
        # CAD 优先走矢量页：保留 12 万实体的真实几何，缩放不糊、还能量尺寸。
        # 不再要求 pdf_ready —— 矢量渲染不依赖引擎的 PDF；失败时页面会给位图入口。
        target = "/cadv/%s?kkt=%s" % (fk, quote(token))
    elif mode == "pdf_local" and sheet.local_pdf_ready(fk):
        # 网关用新版 LibreOffice 转出的 PDF: 交给浏览器内置查看器渲染(矢量、清晰)
        target = "/pdfview/%s?kkt=%s" % (fk, quote(token))
    elif (mode == "sheet" and sheet.sheet_ready(fk)
            and not await engine.pdf_preferred(engine.name_of(info["url"]))):
        target = "/sheet/%s?kkt=%s" % (fk, token)
    elif config.TEXT_DIRECT and _ext0 in config.TEXT_EXTS:
        # 纯文本自己出页面：交给引擎实测 13~153 秒（它要为一篇 txt 起整套转换流程），
        # 而网关读字节 + 猜编码 + <pre> 是毫秒级。
        target = "/textview/%s?kkt=%s" % (fk, quote(token))
    else:
        target = engine_target
    resp = RedirectResponse(url=target)
    resp.set_cookie("kk_pv", token, httponly=True, samesite="lax", path="/")
    return resp


class TaskIn(BaseModel):
    fileUrl: str
    empNo: str


@app.post("/api/v1/preview/task")
async def preview_task(body: TaskIn, request: Request,
                       account: dict = Depends(get_app_account)):
    """预热(旧接口): 把文件放入**引擎**转码队列。

    注意: xlsx/md/Office 现在由网关本地转换, 这个接口对它们无效,
    外部系统请改用 /api/v1/preview/warm。
    """
    request.state.app_id = account["app_id"]
    try:
        engine.check_source_allowed(body.fileUrl)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    result = await engine.add_task(body.fileUrl)
    return ok({"fileUrl": body.fileUrl, "engineReply": result})


class WarmIn(BaseModel):
    fileUrl: str
    empNo: str
    fileName: str = ""


@app.post("/api/v1/preview/warm")
async def preview_warm(body: WarmIn, request: Request,
                       account: dict = Depends(get_app_account)):
    """预热(推荐): 立即返回, 后台按正式预览链路转好并缓存。

    外部系统在"文件列表/详情页"阶段调用, 用户真正点开时就是 0 秒。
    """
    request.state.app_id = account["app_id"]
    request.state.ev.update({"app_id": account["app_id"], "emp_no": body.empNo,
                             "file_name": body.fileName, "action": "warm",
                             "result": "success"})
    try:
        engine.check_source_allowed(body.fileUrl)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    fkey = engine.file_key_of(body.fileUrl)
    if await engine.is_ready(fkey):
        return ok({"status": "ready", "fileKey": fkey})
    active = await engine.claimed_task(fkey)
    if active and await engine.task_alive(active):
        return ok({"status": "warming", "fileKey": fkey, "taskId": active,
                   "pollUrl": "%s/api/v1/preview/status?taskId=%s"
                              % (config.PUBLIC_BASE, active)})
    # 锁过期但任务还在跑（重启、或转换时长超过锁 TTL）：认领回来，别另起一路
    orphan = await engine.active_task(fkey)
    if orphan:
        await engine.task_claim(fkey, orphan)
        return ok({"status": "warming", "fileKey": fkey, "taskId": orphan,
                   "pollUrl": "%s/api/v1/preview/status?taskId=%s"
                              % (config.PUBLIC_BASE, orphan)})
    candidate = uuid.uuid4().hex
    if not await engine.task_claim(fkey, candidate):
        active = await engine.claimed_task(fkey)
        # 抢不到就不起任务。原来这里会用自己的 candidate 继续插任务转换，
        # 等于绕过锁，同一文件被转两遍。
        if not active:
            return ok({"status": "converting", "fileKey": fkey, "taskId": "",
                       "tip": "该文件正在由其他请求转换，稍后用 previewUrl 打开即可"})
        return ok({"status": "warming", "fileKey": fkey, "taskId": active,
                   "pollUrl": "%s/api/v1/preview/status?taskId=%s"
                              % (config.PUBLIC_BASE, active)})
    task_id = candidate
    fname = body.fileName or engine.name_of(body.fileUrl)
    await db.execute(
        "INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url, file_name, status)"
        " VALUES (%s,%s,%s,%s,%s,%s,'queued')",
        (task_id, account["app_id"], body.empNo, fkey, body.fileUrl, fname[:500]))
    _spawn_convert(fkey, body.fileUrl, task_id, fname)
    return ok({"status": "warming", "fileKey": fkey, "taskId": task_id,
               "pollUrl": "%s/api/v1/preview/status?taskId=%s"
                          % (config.PUBLIC_BASE, task_id)})


# ------------------------------------------------------------------ 引擎反向代理
# 预览页注入纯白背景(引擎默认灰底), 可在 .env 用 WHITE_BACKGROUND=0 关闭
WHITE_BG_CSS = ("<style>html,body{background:#fff!important}"
                "#viewerContainer,#viewer,.pdfViewer,.page,.canvasWrapper,.textLayer,"
                ".imagePreview,.office-page{background:#fff!important}"
                ".page{border:1px solid #e3e6ea!important;box-shadow:none!important}</style>")


def inject_white_bg(html: str) -> str:
    if "</head>" in html:
        return html.replace("</head>", WHITE_BG_CSS + "</head>", 1)
    return html + WHITE_BG_CSS


# pdf.js viewer 在 iframe 里, 不会被上面的注入覆盖, 默认仍是深色底 —— 单独注入
PDFJS_WHITE_CSS = """
<style>
:root{--body-bg-color:#fff!important;--viewer-container-bg-color:#fff!important;
 --main-color:#222!important;--toolbar-bg-color:#f6f7f9!important;
 --sidebar-bg-color:#fff!important;--toolbar-border-color:#e6e8eb!important;
 --field-bg-color:#fff!important;--progressBar-color:#4a7cf7!important}
html,body,#outerContainer,#mainContainer,#viewerContainer,#toolbarContainer,
#toolbarViewer,#toolbarViewerMiddle,#sidebarContainer,.page,#printContainer{background:#fff!important}
#viewerContainer::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:99999;
 background-image:url("{WM}");background-repeat:repeat}
</style>
<script>
(function(){
  /* 默认按"页宽"显示。
     pdf.js 默认是整页适应, 在宽屏上会把 A4/A3 缩到很小, 文字和细线看着发虚;
     页宽模式大约放大 1.4 倍, 而且 canvas 重绘是按新比例栅格的, 明显更清楚。 */
  var tuned=false;
  function tune(){
    try{
      var app=window.PDFViewerApplication;
      if(!app||!app.pdfViewer) return;
      try{ if(window.pdfjsLib) pdfjsLib.maxCanvasPixels=67108864; }catch(e){}
      if(tuned) return;
      tuned=true;
      if(!/[#&]zoom=/.test(location.hash||'')){
        app.pdfViewer.currentScaleValue='page-width';
      }
    }catch(e){}
  }
  document.addEventListener('webviewerloaded', function(){ setTimeout(tune,150); });
  window.addEventListener('load', function(){ setTimeout(tune,250); });
})();
</script>
"""


def _partial_banner(partial: str, fk: str = "", kkt: str = "", ver: int = 0) -> str:
    """大 PPT 只转出了前几页时，在 pdf.js 顶部挂一条提示，避免用户以为文件就这么长。

    同时挂一个轮询（部分版阶段 1.5 秒一次），后台把完整版转好后
    （PDF 版本号 ver 变化 且 partial 标记已清）**自动刷新并停在同一页**，
    不需要用户手动按 F5。
    """
    try:
        cur, total = partial.split("/", 1)
        cur_i, total_i = int(cur), int(total)
    except (ValueError, AttributeError):
        return ""
    if cur_i <= 0 or total_i <= 0:
        return ""
    return """<style>
#kkpart{position:fixed;left:50%%;top:12px;transform:translateX(-50%%);z-index:2147483000;
 max-width:min(680px,calc(100vw - 40px));padding:8px 18px;border-radius:18px;
 background:#fff8e1;border:1px solid #f0c36d;color:#8a5a00;font-size:12.5px;line-height:1.5;
 box-shadow:0 3px 14px rgba(0,0,0,.10);font-family:-apple-system,"Microsoft YaHei",sans-serif}
#kkpart b{font-weight:600;color:#7a4d00}
</style>
<div id="kkpart">高清排版正在后台转换…<b>当前显示前 %(cur)d 页</b>（共 %(total)d 页），
转换完成后会自动更新，无需刷新。</div>
<script>
(function(){
  var fk=%(fk)s, kkt=%(kkt)s, ver=%(ver)d, box=document.getElementById('kkpart'),
      busy=false, mism=0, idle=0;
  function reload(){
    if(busy||!canReload()){ return; }
    busy=true;
    try{ sessionStorage.setItem('kkpr:'+fk, String(Date.now())); }catch(e){}
    var p=0;
    try{ p=(window.PDFViewerApplication&&PDFViewerApplication.page)||0; }catch(e){}
    if(p>1){ location.hash='page='+p; }        /* 回来后停在原来那一页 */
    location.reload();
  }
  function hide(){                             /* 服务端已无部分版标记: 横幅不该在 */
    if(box&&box.parentNode){ box.parentNode.removeChild(box); }
    box=null;
  }
  function next(ms){ setTimeout(poll, ms); }
  function canReload(){                        /* 6 秒内已自动刷过就不再刷, 防止刷新死循环 */
    try{
      var last=+sessionStorage.getItem('kkpr:'+fk)||0;
      return Date.now()-last > 6000;
    }catch(e){ return true; }
  }
  function poll(){
    fetch('/api/v1/preview/partial?f='+encodeURIComponent(fk)+'&kkt='+encodeURIComponent(kkt),
          {cache:'no-store'})
      .then(function(r){return r.json()})
      .then(function(d){
        if(!d){ next(5000); return; }
        var changed = ver>0 && d.ver>0 && d.ver!==ver;
        /* 换成新文件 + 标记已清 → 刷新。两个条件都满足才刷,
           否则会带着"部分版"横幅刷进来(替换文件与清标记差几毫秒)。 */
        if(changed && !d.partial && d.ready){ reload(); return; }
        if(changed){
          /* 标记残留: 连等 3 次(4.5 秒)仍不清就强行刷, 防止卡在 15 页 */
          if(++mism>=3){ reload(); return; }
          next(1500); return;
        }
        mism=0;
        if(!d.partial){ hide(); if(++idle>18){ return; } next(2500); return; }
        idle=0;
        if(box){
          var m=String(d.partial).split('/');
          if(m.length===2){
            box.innerHTML='高清排版正在后台转换…<b>当前显示前 '+m[0]+' 页</b>（共 '+m[1]+
                          ' 页），转换完成后会自动更新，无需刷新。';
          }
        }
        next(1500);
      })
      .catch(function(){ next(5000); });
  }
  poll();
})();
</script>""" % {"cur": cur_i, "total": total_i,
                 "fk": json.dumps(fk), "kkt": json.dumps(kkt), "ver": int(ver or 0)}


def _stamp_assets(html: str) -> str:
    """给 pdf.js 的固定 URL 资源加版本戳，强制浏览器拉新文件。

    不这么做的话，升级引擎后会出现「HTML 是新的、viewer.js 是浏览器缓存里的旧版」，
    两者 DOM 结构对不上 → SecondaryToolbar / PDFDocumentProperties 拿到 null →
    `Cannot read properties of null (reading 'addEventListener')`
    以及 `OverlayManager.register: Not enough parameters`。
    """
    ver = config.ASSET_VER
    for a, b in (('src="viewer.js"', 'src="viewer.js?v=%s"' % ver),
                 ('href="viewer.css"', 'href="viewer.css?v=%s"' % ver),
                 ('src="../build/pdf.js"', 'src="../build/pdf.js?v=%s"' % ver)):
        html = html.replace(a, b)
    return html


def inject_pdfjs(html: str, wm_url: str, partial: str = "",
                 fk: str = "", kkt: str = "", ver: int = 0) -> str:
    """给 pdf.js viewer 注入纯白背景 + 水印(与整体风格和水印要求保持一致)。"""
    css = PDFJS_WHITE_CSS.replace("{WM}", wm_url)
    if partial:
        css += _partial_banner(partial, fk, kkt, ver)
    html = _stamp_assets(html)
    if "</head>" in html:
        return html.replace("</head>", css + "</head>", 1)
    return html + css


# 入口类: 必须有预览令牌
PROXY_GUARD = ("onlinePreview", "picturesPreview", "fileUpload", "deleteFile")
# 文件流类: 有令牌就校验令牌; 无令牌但来自可信内嵌域名(含本服务自身)则放行
PROXY_FILE = ("getCorsFile",)

# 引擎遇到不支持的格式会吐一张"系统暂不支持在线预览"页：sorry.jpg + 品牌 + 理由。
# 产品要求：只留一句话，其它内容（图、品牌、说明）一概不显示。
_UNSUPPORTED_MARK = "系统暂不支持在线预览"
_UNSUPPORTED_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>格式暂时不支持预览</title>
<style>html,body{height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:#fff;
 color:#5b636e;font:14px/1.7 -apple-system,"Microsoft YaHei","PingFang SC",sans-serif}</style>
</head><body>格式暂时不支持预览</body></html>"""


# 企业文档加密系统（TSD/亿赛通等）包裹的文件：服务器拿到的是密文，
# 任何转换引擎都打不开。这类文件必须"立刻说清楚"，而不是让人对着
# "正在转换"等半分钟——实测 13MB 的加密 pptx 会白等 33.9 秒最后仍是空白。
_ENCRYPTED_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>受文档加密保护</title>
<style>html,body{height:100%%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:#fff;
 color:#5b636e;font:14px/1.9 -apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
.box{text-align:center;padding:0 24px}
h1{margin:0 0 8px;font-size:15px;font-weight:600;color:#222}
p{margin:0;color:#8a9099;font-size:13px}
</style></head><body><div class="box">
<h1>%(msg)s</h1>
<p>请在安装了加密客户端的电脑上打开原文件</p>
</div></body></html>"""


def encrypted_msg(system: str) -> str:
    """加密文件的统一提示语。接口 422 的 detail 与预览页正文都用这一句。"""
    return "该文件被%s保护，服务器无法解密预览" % (system or "文档加密系统")


_EMPTY_MODEL_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>该模型没有可显示的实体面</title>
<style>html,body{height:100%%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:#fff;
 color:#5b636e;font:14px/1.9 -apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
.box{text-align:center;padding:0 24px;max-width:560px}
h1{margin:0 0 8px;font-size:15px;font-weight:600;color:#222}
p{margin:0;color:#8a9099;font-size:13px}
</style></head><body><div class="box">
<h1>该模型没有可显示的实体面，无法 3D 预览</h1>
<p>%(reason)s<br>3D 预览只画实体面；曲线、曲面、基准面不参与渲染。<br>
需要查看结构请用 CAD 软件打开原文件</p>
</div></body></html>"""


def _empty_model_page(reason: str) -> str:
    import html as _html
    return _EMPTY_MODEL_PAGE % {"reason": _html.escape(reason or "预解析结果为空")}


def _encrypted_page(reason: str) -> str:
    import html as _html
    return _ENCRYPTED_PAGE % {"msg": _html.escape(encrypted_msg(reason))}


# 首次加载 STEP（还没有网格）时的等待页。页面自己轮询 /stepstate，
# 好了直接跳 3D 页面，用户不用手动刷新。
_CADV_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;overflow:hidden;background:#f4f5f7;
 font:13px -apple-system,"Microsoft YaHei",sans-serif;color:#3a3f47}
#bar{height:36px;display:flex;align-items:center;gap:6px;padding:0 10px;background:#fff;
 border-bottom:1px solid #e3e6ea;position:relative;z-index:5}
#bar .ttl{font-weight:600;max-width:34%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#bar .sp{flex:1}
#bar button{border:1px solid #cfd4da;background:#fff;border-radius:4px;padding:3px 8px;
 font-size:12px;cursor:pointer;color:#3a3f47}
#bar button.on{background:#2f6feb;border-color:#2f6feb;color:#fff}
#bar a{border:1px solid #cfd4da;border-radius:4px;padding:3px 8px;font-size:12px;
 text-decoration:none;color:#3a3f47}
#info{color:#8a9099;font-size:11.5px}
#stage{position:absolute;inset:36px 0 0 0;overflow:hidden;cursor:grab;background:#fff}
#stage.drag{cursor:grabbing}
#wrap{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}
#wrap svg{display:block}
#layPanel{position:fixed;right:12px;top:44px;z-index:14;width:200px;display:none;
 background:rgba(255,255,255,.97);border:1px solid #e3e6ea;border-radius:7px;
 box-shadow:0 2px 12px rgba(20,30,50,.12);padding:8px 10px;font-size:12px;max-height:60%;overflow:auto}
#layPanel.on{display:block}
#layPanel .r{display:flex;gap:7px;align-items:center;padding:3px 0;cursor:pointer}
#layPanel .r span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#tip{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);background:#2f6feb;color:#fff;
 padding:5px 12px;border-radius:14px;font-size:12px;display:none;z-index:9}
#tip.on{display:block}
#ld{position:absolute;inset:36px 0 0 0;background:rgba(255,255,255,.92);display:flex;
 align-items:center;justify-content:center;flex-direction:column;gap:10px;z-index:6}
#ld.hide{display:none}
.sp2{width:30px;height:30px;border:3px solid #e6e8eb;border-top-color:#2f6feb;border-radius:50%;
 animation:r .9s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
.steps{margin:14px auto 0;min-width:288px;text-align:left}
.st{display:flex;gap:9px;align-items:baseline;font-size:12px;color:#9aa1ab;
 padding:3px 0;line-height:1.5}
.st b{flex:none;width:78px;font-weight:600;color:#a4abb4}
.st.run b{color:#2f6feb}
.st.run b::after{content:'…'}
.st.done b{color:#22a06b}
.st.done b::after{content:' ✓'}
.st i{font-style:normal;color:#8a9099}
.st.done i{color:#6b7280}
</style></head><body>
<div id="bar">
  <span class="ttl">__TITLE__</span>
  <span id="info"></span>
  <span class="sp"></span>
  <button id="zo">－</button><button id="zi">＋</button>
  <button id="fit">适应</button>
  <button id="layBtn" style="display:none">图层</button>
  <button id="meas">量尺寸</button>
  <button id="reset">复位</button>
  <a id="bmp" href="/cadview/__FK__?kkt=" title="换成引擎栅格化的位图（文字缺失时可对照）">位图</a>
</div>
<div id="stage"><div id="wrap"></div></div>
<div id="ld"><div class="sp2"></div><div id="ldt">正在把 DWG 转成矢量图…</div>
  <div class="steps" id="ldSteps"></div></div>
<div id="layPanel"></div>
<div id="tip">点击两个点测量（Esc 退出）</div>
<script>
var FK=__FK__, KKT=__KKT__, svgEl=null, vb=null, UNIT='';
var k=1, tx=0, ty=0, stage=document.getElementById('stage'), wrap=document.getElementById('wrap');
var ld=document.getElementById('ld'), ldt=document.getElementById('ldt'), info=document.getElementById('info');
var tip=document.getElementById('tip'), meas=false, pts=[], marks=[];

function apply(){ wrap.style.transform='translate('+tx+'px,'+ty+'px) scale('+k+')'; }
function zoom(f, cx, cy){
  var nk=Math.min(60, Math.max(0.02, k*f));
  var r=nk/k;
  if(cx!==undefined){ tx = cx-(cx-tx)*r; ty = cy-(cy-ty)*r; }
  k=nk; apply();
}
function fitView(){
  if(!vb) return;
  var W=stage.clientWidth, H=stage.clientHeight;
  // 图纸普遍很扁（这张 4.27:1，窗口 1.6:1），按高度适应会剩一大片空白。
  // 优先按宽度铺满；只有当高度超出窗口 15% 以上时才退回"完整适应"。
  k = W / vb.w * 0.98;
  if (vb.h * k > H * 1.15) k = Math.min(W / vb.w, H / vb.h) * 0.96;
  // svg 的 width/height 已设成 vb.w/vb.h，本地坐标从 0 起，viewBox 的平移
  // 由 SVG 自己做了。这里再减一次 vb.x*k / vb.y*k 会把图顶到左上角
  // （实测图纸跑到画面左上、下面空一大片）。
  tx=(W-vb.w*k)/2; ty=(H-vb.h*k)/2;
  apply();
}

/* 阶段日志：下载 → 解析 → 生成 → 构建，每步带耗时，用户能看到卡在哪 */
function stepLog(key, name, detail, state){
  var box=document.getElementById('ldSteps');
  if(!box) return;
  var el=document.getElementById('st_'+key);
  if(!el){ el=document.createElement('div'); el.id='st_'+key; box.appendChild(el); }
  el.className='st '+(state||'run');
  el.innerHTML='<b>'+name+'</b><i>'+(detail||'')+'</i>';
}
function showProg(p, since, final){
  if(!p || !p.stage) return;
  var sec = function(ms){ return ms==null ? '' : (ms/1000).toFixed(1)+'s'; };
  if(p.stage==='dl'){
    stepLog('dl','下载源文件','连接源站 · '+((Date.now()-since)/1000).toFixed(1)+'s','run');
  } else if(p.stage==='parse' || p.stage==='done'){
    stepLog('dl','下载源文件', (p.bytes? (p.bytes/1048576).toFixed(1)+' MB · ':'')+sec(p.dlMs), 'done');
    if(p.stage==='parse'){
      stepLog('ps','解析 DWG', (p.entities? p.entities.toLocaleString()+' 个实体 · ':'')+sec(p.parseMs), 'run');
    } else {
      stepLog('ps','解析 DWG', (p.entities? p.entities.toLocaleString()+' 个实体 · ':'')+sec(p.parseMs), 'done');
      stepLog('rd','生成矢量图',
        (p.drawn? p.drawn.toLocaleString()+' 个图元 · ':'')+sec(p.renderMs), 'done');
    }
  }
  if(p.stage==='fail'){
    ldt.textContent='渲染失败：'+(p.error||'未知原因');
  }
}

// 加载：先问网关有没有成品，没有就等它渲染（约 4 秒）
function load(){
  var since = (load._t || (load._t = Date.now()));
  // 不带 ?kkt=：URL 稳定，浏览器才能命中缓存；令牌走 X-Preview 头。
  // 服务端有 ETag，缓存过期会走 304（几百字节），不会重新下整张图。
  fetch('/cadvec/'+FK, {headers:{'X-Preview':KKT}})
    .then(function(r){
      if(r.status===202) return r.json().then(function(j){
        if(j.prog) showProg(j.prog, since);
        throw {wait:true, j:j};
      });
      if(!r.ok) throw new Error('HTTP '+r.status);
      var _hp = r.headers.get('x-cad-prog');
      if(_hp){ try { showProg(JSON.parse(_hp), since, true); } catch(e) {} }
      return r.text();
    })
    .then(function(txt){
      wrap.innerHTML = txt;
      svgEl = wrap.querySelector('svg');
      UNIT = svgEl.getAttribute('data-units') || '';   // mm / cm / in…，空表示图纸未设单位
      var vbAttr = svgEl.getAttribute('viewBox').trim().split(/ +/).map(Number);
      vb = {x:vbAttr[0], y:vbAttr[1], w:vbAttr[2], h:vbAttr[3]};
      svgEl.setAttribute('width', vb.w); svgEl.setAttribute('height', vb.h);
      fitView();
      var _el=((Date.now()-since)/1000).toFixed(1);
      if(!document.getElementById('st_dl') && !document.getElementById('st_rd')){
        // 一次 200 就拿到：说明是缓存里的成品，没有渲染过程可显示
        stepLog('cp','读取矢量图', Math.round(vb.w)+' × '+Math.round(vb.h)+' 单位 · '+_el+'s', 'done');
      } else if(!document.getElementById('st_bd')) {
        stepLog('bd','构建视图', Math.round(vb.w)+' × '+Math.round(vb.h)+' 单位 · '+_el+'s', 'done');
      }
      ld.classList.add('hide');
      info.textContent = '图纸 ' + Math.round(vb.w) + ' × ' + Math.round(vb.h) +
                        ' ' + (UNIT || '单位') + ' · 矢量';
      buildLayers();
    })
    .catch(function(e){
      if(e && e.wait){
        // 后端明确报了失败：别再无限等，给一个位图版入口
        if(e.j && e.j.state === 'failed'){
          ldt.innerHTML = '矢量渲染失败：' + (e.j.detail || '未知原因') +
            '<br><a style="color:#2f6feb;margin-top:8px;display:inline-block" ' +
            'href="/cadview/' + FK + '?kkt=' + encodeURIComponent(KKT) + '">查看位图版</a>';
          return;
        }
        setTimeout(load, 1200); return;
      }
      ldt.textContent = '渲染失败：' + (e && e.message ? e.message : e);
    });
}
load();

stage.addEventListener('wheel', function(e){
  e.preventDefault();
  var r=stage.getBoundingClientRect();
  zoom(e.deltaY<0?1.15:1/1.15, e.clientX-r.left, e.clientY-r.top);
}, {passive:false});

var down=false, lx=0, ly=0;
stage.addEventListener('mousedown', function(e){
  if(meas) return;
  down=true; lx=e.clientX; ly=e.clientY; stage.classList.add('drag');
});
window.addEventListener('mousemove', function(e){
  if(!down) return;
  tx+=e.clientX-lx; ty+=e.clientY-ly; lx=e.clientX; ly=e.clientY; apply();
});
window.addEventListener('mouseup', function(){ down=false; stage.classList.remove('drag'); });

// 量尺寸：把屏幕点换算成图纸坐标，两点距离就是真实尺寸
function toSvg(ev){
  if(!svgEl) return null;
  var p=svgEl.createSVGPoint(); p.x=ev.clientX; p.y=ev.clientY;
  var m=svgEl.getScreenCTM(); if(!m) return null;
  return p.matrixTransform(m.inverse());
}
function mark(a, b){
  var g=document.createElementNS('http://www.w3.org/2000/svg','g');
  var l=document.createElementNS('http://www.w3.org/2000/svg','line');
  l.setAttribute('x1',a.x); l.setAttribute('y1',a.y);
  l.setAttribute('x2',b.x); l.setAttribute('y2',b.y);
  l.setAttribute('stroke','#e0245e'); l.setAttribute('stroke-width', Math.max(0.5, vb.w/900));
  g.appendChild(l);
  [a,b].forEach(function(p){
    var c=document.createElementNS('http://www.w3.org/2000/svg','circle');
    c.setAttribute('cx',p.x); c.setAttribute('cy',p.y);
    c.setAttribute('r', Math.max(1, vb.w/500)); c.setAttribute('fill','#e0245e');
    g.appendChild(c);
  });
  svgEl.appendChild(g); marks.push(g);
  return Math.hypot(b.x-a.x, b.y-a.y);
}
stage.addEventListener('click', function(ev){
  if(!meas) return;
  var p=toSvg(ev); if(!p) return;
  pts.push(p);
  if(pts.length===2){
    var d=mark(pts[0], pts[1]);
    tip.textContent='距离 ' + d.toFixed(2) + ' ' + (UNIT || '图纸单位') +
                   ' · 继续点两个点可再测（Esc 退出）';
    pts=[];
  } else {
    tip.textContent='已选起点，再点一个点';
  }
});
/* 图层开关：SVG 里每个 path 带 data-layer，按层收集。只有一层时按钮隐藏
   （很多 DWG 出图时不分层，露个"0 层"没意义）。 */
function buildLayers(){
  var ps = svgEl.querySelectorAll('path[data-layer]');
  var map = {}, order = [];
  for(var i=0;i<ps.length;i++){
    var L = ps[i].getAttribute('data-layer') || '0';
    if(!map[L]){ map[L] = []; order.push(L); }
    map[L].push(ps[i]);
  }
  if(order.length <= 1) return;
  var btn = document.getElementById('layBtn'), panel = document.getElementById('layPanel');
  btn.style.display = '';
  btn.onclick = function(){
    panel.classList.toggle('on');
    this.classList.toggle('on', panel.classList.contains('on'));
  };
  panel.innerHTML = '';
  order.forEach(function(L){
    var r = document.createElement('label');
    r.className = 'r';
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true;
    cb.onchange = function(){
      map[L].forEach(function(p){ p.style.display = cb.checked ? '' : 'none'; });
    };
    var sp = document.createElement('span');
    sp.textContent = L + ' (' + map[L].length + ')';
    sp.title = L;
    r.appendChild(cb); r.appendChild(sp); panel.appendChild(r);
  });
}

document.getElementById('meas').onclick=function(){
  meas=!meas; this.classList.toggle('on', meas);
  tip.classList.toggle('on', meas);
  tip.textContent = meas ? '点击两个点测量（Esc 退出）' : '';
  pts=[];
};
document.getElementById('zi').onclick=function(){ zoom(1.2, stage.clientWidth/2, stage.clientHeight/2); };
document.getElementById('zo').onclick=function(){ zoom(1/1.2, stage.clientWidth/2, stage.clientHeight/2); };
// 位图入口要带上真实的令牌（页面里的 a 标签没法带头部）
(function(){
  var a=document.getElementById('bmp');
  if(a) a.href='/cadview/'+FK+'?kkt='+encodeURIComponent(KKT);
})();
document.getElementById('fit').onclick=fitView;
document.getElementById('reset').onclick=function(){
  marks.forEach(function(g){ g.remove(); }); marks=[]; pts=[];
  fitView();
};
window.addEventListener('keydown', function(e){
  if(e.key==='Escape'){ document.getElementById('meas').click(); }
});
</script>
</body></html>"""

_STEP_WAIT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>正在解析 3D 模型</title>
<style>
html,body{margin:0;height:100%;background:#f4f5f7;
 font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#3a3f47}
.box{position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);text-align:center}
.sp{width:34px;height:34px;margin:0 auto 16px;border:3px solid #e6e8eb;
 border-top-color:#2f6feb;border-radius:50%;animation:sp .9s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
h3{margin:0 0 8px;font-size:16px;font-weight:600}
p{margin:4px 0;color:#6b7280}
b{color:#2f6feb}
.tip{font-size:12.5px;color:#9aa1ab;margin-top:10px}
.steps{margin:20px auto 0;min-width:300px;text-align:left}
.st{display:flex;gap:9px;align-items:center;font-size:12.5px;color:#8a9099;padding:6px 0}
.st b{flex:none;width:70px;font-weight:600;color:#9aa1ab}
.st.run b{color:#2f6feb}
.st.run b::after{content:'…'}
.st.done b{color:#22a06b}
.st.done b::after{content:' ✓'}
.st i{font-style:normal;flex:none}
.st .bar{display:none;flex:1;height:4px;background:#e9ecef;border-radius:2px;
 overflow:hidden;min-width:70px;margin-left:4px}
.st.run .bar{display:block}
.st .fi{height:100%%;width:0;background:#2f6feb;border-radius:2px;transition:width .5s}
</style></head><body>
<div class="box">
  <div class="sp"></div>
  <h3>正在首次解析这个 3D 模型</h3>
  <p id="msg">已等待 <b id="s">0</b> 秒，转好会自动打开，不用刷新。</p>
  <div class="steps">
    <div class="st run" id="s1"><b>受理</b><i id="s1i">已加入解析队列</i></div>
    <div class="st" id="s2"><b>解析模型</b><i id="s2i">等待开始</i><div class="bar"><div class="fi" id="s2b"></div></div></div>
    <div class="st" id="s3"><b>生成网格</b><i id="s3i">等待中</i></div>
  </div>
  <p class="tip" id="tip2" style="display:none"></p>
  <p class="tip">大装配体通常 1~5 分钟；也可以先看别的文件，稍后再回来。</p>
</div>
<script>
var FK=__FK__, KKT=__KKT__, T0=Date.now(), TIMER=0;
function tick(){var e=document.getElementById('s'); if(e) e.textContent=Math.round((Date.now()-T0)/1000);}
TIMER=setInterval(tick,1000); tick();
function stop(){ if(TIMER){clearInterval(TIMER); TIMER=0;} }
(function loop(i){
  i = i || 0;
  if (i > 240) {
    stop();
    var m = document.getElementById('msg');
    if (m) m.innerHTML = '仍在解析，稍后重新打开这个文件即可。';
    return;
  }
  setTimeout(function(){
    fetch('/stepstate/' + FK + '?kkt=' + encodeURIComponent(KKT), {cache:'no-store'})
      .then(function(r){ return r.json(); })
      .then(function(j){
        if (j.state === 'ready') { stop(); location.replace('/stepview/' + FK + '?kkt=' + encodeURIComponent(KKT)); return; }
        if (j.state === 'empty') { stop(); location.replace('/stepempty/' + FK + '?kkt=' + encodeURIComponent(KKT)); return; }
        if (j.state === 'failed') {
          stop();
          var m = document.getElementById('msg');
          if (m) m.innerHTML = '解析失败，请联系管理员（文件可能被加密、需要登录，或几何异常）';
          return;
        }
        function setSt(id, cls, txt) {
          var el = document.getElementById(id);
          if (el) { el.className = 'st ' + cls; }
          var t = document.getElementById(id + 'i');
          if (t && txt) { t.textContent = txt; }
        }
        if (j.prog) {
          var q = String(j.prog).split('/');
          var done = parseInt(q[0], 10), total = parseInt(q[1], 10);
          if (total > 0) {
            setSt('s1', 'done', '已受理');
            setSt('s2', 'run', '正在解析 ' + done + ' / ' + total + ' 个零件');
            var fi = document.getElementById('s2b');
            if (fi) { fi.style.width = Math.min(100, done * 100 / total).toFixed(0) + '%'; }
          }
        }
        if (j.state === 'ready' && j.mode === 'wire') {
          setSt('s1', 'done', '已受理');
          setSt('s2', 'done', '零件解析完成');
          setSt('s3', 'run', '实体网格生成中');
        }
        loop(i + 1);
      })
      .catch(function(){ loop(i + 1); });
  }, 2000);
})();
</script></body></html>"""


def _step_wait_page(file_key: str, kkt: str) -> str:
    return (_STEP_WAIT_PAGE
            .replace("__FK__", json.dumps(file_key))
            .replace("__KKT__", json.dumps(kkt)))


# 引擎的 watermark.js 在页面加载时按 body.scrollHeight 铺一遍，但它监听的是
# ``window.addEventListener('onload', ...)`` —— 事件名拼错了（应为 'load'），
# 永远不会触发；只有 resize 会重铺。于是"表格只有几行、内容后来才撑高"的页面
# 水印只覆盖上半截，往下滚就是白板（csv/xls 走引擎时实测如此）。
# 这里在同源页面里替它触发几次 resize，让它按最终高度重新生成。
_WM_REFIT = """<script>
(function () {
  function refit() { try { window.dispatchEvent(new Event('resize')); } catch (e) {} }
  window.addEventListener('load', function () {
    setTimeout(refit, 400);
    setTimeout(refit, 1200);
    setTimeout(refit, 2500);
  });
})();
</script>"""


def _brand(html: str) -> str:
    """把引擎自带的 kkFileView 字样换成产品名。

    只替换文本节点（`>kkFileView<`），不动 `kkFileView-4.4.0.jar` 这类路径，
    否则会改坏静态资源地址。
    """
    name = (config.BRAND_NAME or "").strip()
    return html.replace(">kkFileView<", ">" + name + "<") if name else html
# 静态资源: 直接放行(无敏感数据, 且 iframe 内页面会请求)
EMBED_STATIC_HDRS = ("x-frame-options",)


async def cors_file_proxy(request: Request):
    """自己代理 getCorsFile 的文件流，**不再转给引擎**。

    为什么必须绕开引擎（2026-10-22 定位）：
        引擎的 getCorsFile 把解码后的 URL 交给 galimatias 规范化，再调
        `url.toURI()`；而 java.net.URL#toURI 对含中文/空格的 query 会抛
        URISyntaxException。kb-portal 的地址正是 `...?path=1-3 受控文件/...`
        这种形态，于是每次都在这里炸掉 —— 而引擎那段代码是
        `catch (Exception e) { System.out.println(e); }`，异常被吞、响应体为空。
        结果是 HTTP 200 但 0 字节，pdf.js 拿到空内容就渲染成一片空白。

    另外顺带解决两个老问题：
        - 引擎转发时丢 content-type，pdf.js 只能靠魔数猜；
        - 浏览器带 If-None-Match 时引擎回 304，网关会误判成"文件内容为空"。
    """
    raw = request.query_params.get("urlPath", "")
    if not raw:
        raise HTTPException(status_code=400, detail="缺少 urlPath 参数")
    # 表单/查询串里的 + 会被解析成空格，base64 要先还原（与引擎的注释一致）
    b64 = raw.replace(" ", "+")
    b64 += "=" * (-len(b64) % 4)
    try:
        target = base64.urlsafe_b64decode(b64).decode("utf-8")
    except Exception:
        try:
            target = base64.b64decode(b64).decode("utf-8")
        except Exception:
            return None          # 解不出来就交回原来的转发逻辑，不擅自报错
    if not target.lower().startswith(("http://", "https://")):
        # 极少数情况下 urlPath 指向本地路径，这种仍让引擎自己处理
        return None

    client = await engine.http()
    req = client.build_request(
        "GET", target,
        headers={"accept": "*/*", "accept-encoding": "identity"})
    try:
        resp = await client.send(req, stream=True)
    except Exception as exc:
        log.warning("文件下载失败: %s -> %s", target[:120], exc)
        raise HTTPException(status_code=502, detail="文件下载失败: %s" % exc)

    if resp.status_code >= 400:
        await resp.aclose()
        log.warning("源站返回 %s: %s", resp.status_code, target[:120])
        raise HTTPException(
            status_code=502 if resp.status_code >= 500 else 400,
            detail="源站返回 HTTP %s，文件可能已过期或无权访问" % resp.status_code)

    # 关键：content-length 必须原样带回去，pdf.js 靠它判断文件大小，
    # 拿不到就报 "The PDF file is empty, i.e. its size is zero bytes"
    headers = {}
    for k in ("content-length", "content-type", "last-modified", "etag"):
        v = resp.headers.get(k)
        if v:
            headers[k] = v
    if "content-type" not in headers:
        # 引擎原先不给类型；按后缀补一个，避免浏览器/查看器二次猜测
        low = target.split("?")[0].lower()
        guess = {".pdf": "application/pdf"}.get(low[-4:], "")
        if not guess:
            for ext, mime in ((".png", "image/png"), (".jpg", "image/jpeg"),
                              (".jpeg", "image/jpeg"), (".gif", "image/gif"),
                              (".webp", "image/webp"), (".svg", "image/svg+xml")):
                if low.endswith(ext):
                    guess = mime
                    break
        headers["content-type"] = guess or "application/octet-stream"
    # 3D/CAD 源文件（step/stp/iges/obj…）是纯文本，47MB 的装配体压缩后只有 5~6MB。
    # 之前这里一律 identity + no-cache：每次打开都要重新下 47MB，
    # 而 occt-import-js 必须等下完才开始解析，这段等待纯属浪费。
    low_path = target.split("?")[0].lower()
    ext = low_path.rsplit(".", 1)[-1] if "." in low_path else ""
    accepts_gz = "gzip" in (request.headers.get("accept-encoding", "").lower())
    if ext in config.TEXT_SOURCE_EXTS and accepts_gz:
        headers.pop("content-length", None)
        headers["content-encoding"] = "gzip"
        headers["vary"] = "Accept-Encoding"
        headers["cache-control"] = "private, max-age=1800"
        return StreamingResponse(_gzip_stream(resp), status_code=200, headers=headers)

    # 其余文件（含 PDF/图片）透传，允许浏览器缓存 30 分钟：
    # 与转换缓存 TTL 对齐——这段时间内重复打开同一份图纸/模型不再重下
    # （47MB 的 STEP 重下一次就是 1.7 秒 + 浏览器重新解析）。
    headers["cache-control"] = "private, max-age=1800"

    return StreamingResponse(resp.aiter_raw(), status_code=200, headers=headers)


async def _gzip_stream(resp, level: int = 6):
    """边下边压，避免为了压 47MB 先把整个文件读进内存。"""
    comp = zlib.compressobj(level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    try:
        async for chunk in resp.aiter_raw():
            out = comp.compress(chunk)
            if out:
                yield out
        tail = comp.flush()
        if tail:
            yield tail
    finally:
        await resp.aclose()


def _host_of(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _allowed_embed_hosts() -> set:
    hosts = {h.strip() for h in config.EMBED_ALLOWED_HOSTS.split(",") if h.strip()}
    hosts.add(_host_of(config.PUBLIC_BASE))
    return hosts


def _extract_preview_token(request: Request) -> str:
    """按 优先 URL 参数 kkt → Referer 中的 kkt → Cookie 提取预览令牌。

    跨域 iframe 场景下第三方 Cookie 会被浏览器拦截, 所以以 URL/Referer 为准。
    """
    token = request.query_params.get("kkt")
    if token:
        return token
    ref = request.headers.get("referer", "")
    m = re.search(r"[?&]kkt=([A-Za-z0-9_\-.=%]+)", ref)
    if m:
        return unquote(m.group(1))
    return request.cookies.get("kk_pv", "")


@app.api_route("/kk/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def proxy_engine(path: str, request: Request):
    first = path.split("/")[0]
    token = _extract_preview_token(request)
    ref_host = (_host_of(request.headers.get("referer", ""))
                or _host_of(request.headers.get("origin", "")))
    trusted = ref_host in _allowed_embed_hosts()

    info = None
    if first in PROXY_GUARD:
        if not token:
            raise HTTPException(status_code=401, detail="请先通过 /api/v1/preview/page 获取预览地址")
        info = security.parse_preview_token(token)
    elif first in PROXY_FILE:
        if token:
            info = security.parse_preview_token(token)
        elif not trusted:
            raise HTTPException(status_code=401, detail="文件读取请求来源不可信, 请从预览页内发起")
        # 鉴权过了就自己下载，不再转给引擎：
        # 引擎遇到含中文/空格的 query 会抛异常并吞掉，返回 0 字节（PDF 空白的主因）
        proxied = await cors_file_proxy(request)
        if proxied is not None:
            return proxied
    elif token:
        try:
            info = security.parse_preview_token(token)
        except HTTPException:
            info = None
    # 从令牌里取出调用系统, 让预览页访问也能归属到具体系统
    if info and info.get("app"):
        request.state.app_id = info["app"]
    upstream = "%s/%s" % (config.KK_BASE, path)
    if request.url.query:
        upstream += "?" + request.url.query
    client = await engine.http()
    body = await request.body()
    # pdf.js 的 viewer.html 要按实时状态注入横幅/轮询脚本 → 每次都必须拿到完整 HTML，
    # 所以不能带上 If-None-Match/If-Modified-Since（否则上游回 304，我们无内容可注入）。
    is_pdfjs = path.startswith("pdfjs/web/viewer.html")
    # 强制上游返回未压缩内容：
    # 4.4.0 的 Jetty 会给静态 js/css 回 gzip，而我们下面用 aiter_raw() 透传原始字节、
    # 同时把 content-encoding 头过滤掉了，再经 _SelectiveGZip 压一次就成了"双层 gzip"，
    # 浏览器解一层后拿到的是 gzip 字节 → base64.min.js 报 SyntaxError → PDF 页面空白。
    # 一律不转发条件请求头：引擎给产物/CDN 缓存加了 ETag，回 304 时我们没有内容可返回，
    # 既无法注入横幅，又会被下面的"空内容"判断当成坏文件（历史故障：CAD 的 .pdf/.svg 第二次
    # 打开必 502）。这里让引擎每次都回 200 全量，产物本身就是从它的本地缓存读，代价很小。
    _skip_req = ["host", "content-length", "accept-encoding",
                 "if-none-match", "if-modified-since"]
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _skip_req}
    fwd_headers["accept-encoding"] = "identity"
    req = client.build_request(
        request.method, upstream, headers=fwd_headers, content=body or None)
    try:
        # 预览页 HTML 需要注入样式(纯白背景)和加载计时脚本, 所以这里缓冲读取;
        # 文件流仍走流式避免占内存。LOAD_METRIC 开启时即使不要白底也必须缓冲,
        # 否则 STEP 这类纯引擎页面没法注入上报，加载耗时永远是 0。
        # 注意 STEP 的 3D 页面是「onlinePreview → iframe website/index.html」两层：
        # 只注入外层没用，模型在 iframe 里画，所以任何 .html 都要注入。
        is_html_page = (first in PROXY_GUARD or is_pdfjs
                        or path.split("?")[0].endswith(".html"))
        # HTML 页面一律缓冲：除了白底与计时上报，还要改写品牌名、
        # 把引擎的"暂不支持"页换成统一提示，这些与那两个开关无关。
        need_inject = is_html_page
        upstream_resp = await client.send(req, stream=not need_inject)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="预览引擎不可达: %s" % exc)
    # 过滤 X-Frame-Options: 允许业务系统用 iframe 内嵌预览页(引擎本身未设置, 这里兜底)
    headers = {k: v for k, v in upstream_resp.headers.items()
               if k.lower() not in SKIP_HEADERS and k.lower() not in EMBED_STATIC_HDRS}
    # 引擎页面/脚本/样式不缓存：出过一次"双层 gzip 的 base64.min.js 被浏览器缓存，
    # 修好服务端后用户仍是空白 PDF"的坑，这里强制每次回源校验。
    # 静态资源按"会不会变"分三档：
    #   .wasm            → 一周+immutable。STEP 的 3D 查看器每次都要 4.8MB 的
    #                      occt-import-js.wasm，原来没有 Cache-Control，浏览器
    #                      每次重新下载 + 重新编译，光这一项就 1.5 秒。
    #   图片/字体/图标    → 一天。UI 皮肤，变了也就是图标不对，不值得每次回源。
    #   .html / 预览壳页  → no-cache（页面本身要注入状态，必须回源校验）
    low_path = path.split("?")[0].lower()
    if low_path.endswith(".wasm"):
        headers["cache-control"] = "public, max-age=604800, immutable"
    elif low_path.endswith((".js", ".css", ".html")) or first in PROXY_GUARD:
        headers["cache-control"] = "no-cache, must-revalidate"
    elif low_path.endswith((".woff", ".woff2", ".ttf", ".otf", ".png", ".jpg",
                            ".jpeg", ".gif", ".svg", ".ico")):
        headers["cache-control"] = "public, max-age=86400"
    if is_pdfjs:
        # pdf.js 的 viewer.html 会按 Redis 里的实时状态注入进度横幅与轮询脚本，
        # **同一 URL 的内容会随时间变化**，所以必须 no-store：
        # 用 no-cache 时浏览器收到 304 会复用旧副本，那份 HTML 里带着"部分版横幅 +
        # 旧的 PDF 版本号"，每次 reload 都判断"版本变了"→ 无限自动刷新（页面永远加载不完）。
        headers = {k: v for k, v in headers.items()
                   if k.lower() not in ("etag", "last-modified", "expires",
                                        "content-length")}
        headers["cache-control"] = "no-store, no-cache, must-revalidate"
    if need_inject:
        # 这里 client.send(stream=False) 已经把 body 整块读完，**无论是不是 HTML
        # 都必须用缓冲内容返回**。早期只在 content-type 是 text/html 时返回 HTMLResponse，
        # 其它情况落到下面的 aiter_raw() 分支 → body 已消费 → httpx.StreamConsumed
        # → 预览页 500，前端只看到 {"code":500,"msg":"服务内部错误"}。
        # 触发场景：引擎重启/就绪前返回 404、502、text/plain 错误页等非 HTML 响应。
        headers = {k: v for k, v in headers.items() if k.lower() != "content-length"}
        if "text/html" in upstream_resp.headers.get("content-type", ""):
            html = upstream_resp.text
            if is_pdfjs:
                from . import sheet
                # 进度以 Redis 里的真实标记为准，不能信 URL 上带的 partial：
                # 用户刷新页面时 URL 里还是老参数，照搬会让"完整版已就绪"的页面
                # 一直显示部分版横幅、并且反复触发自动刷新。
                cur_part = ""
                if info and info.get("fk"):
                    cur_part = await engine.partial_of(info["fk"])
                body_txt = inject_pdfjs(html, sheet.watermark_bg_url(), cur_part,
                                        (info or {}).get("fk", ""),
                                        quote(token, safe="") if token else "",
                                        engine.pdf_ver(info["fk"]) if info and info.get("fk") else 0)
                # 引擎自己的 pdf.js 页面也带上报：内容画出来才算"用户看到了"
                body_txt = _brand(body_txt)
                body_txt = loadmetric.inject(body_txt, token or "")
            else:
                if _UNSUPPORTED_MARK in html:
                    # 不支持的格式：直接给统一提示页，不注入白底/计时/水印。
                    # 状态码跟着引擎走（200 才换页，4xx/5xx 保留原样便于排查）。
                    if upstream_resp.status_code == 200:
                        return HTMLResponse(_UNSUPPORTED_PAGE,
                                            status_code=200, headers=headers)
                # 引擎页面(onlinePreview 外壳等)加白底; iframe 里的第三方页面
                # (O3DV 的 website/index.html 自带深色 UI)只注入计时脚本, 不改配色
                body_txt = inject_white_bg(html) if first in PROXY_GUARD else html
                body_txt = _brand(body_txt)
                if "watermark" in body_txt and "</body>" in body_txt:
                    body_txt = body_txt.replace("</body>", _WM_REFIT + "</body>", 1)
                # 引擎页面（STEP/IGES 的 3D 查看器、CAD 的 svg 页等）没有我们的脚本，
                # 只能代理时注入：否则这些文件的"加载耗时"永远是 0。
                body_txt = loadmetric.inject(body_txt, token or "")
            return HTMLResponse(content=body_txt,
                                status_code=upstream_resp.status_code, headers=headers)
        if is_pdfjs:
            # 查看器 HTML 拿不到就明确报错：返回空页面的话，用户只看到一片白，
            # 排查时也看不出是引擎侧的问题。
            raise HTTPException(
                status_code=502,
                detail="查看器页面获取失败：引擎返回 %s（内容非 HTML 或为空）"
                       % upstream_resp.status_code)
        return Response(content=upstream_resp.content,
                        status_code=upstream_resp.status_code, headers=headers)

    # 文件流: 先探第一块。内容为空时给出明确原因, 否则 pdf.js 只会报
    # "The PDF file is empty, i.e. its size is zero bytes", 让人误以为是预览服务的问题。
    is_binary = (first in PROXY_FILE
                 or path.lower().endswith(_SelectiveGZip._RAW_SUFFIX))
    if is_binary:
        if upstream_resp.status_code == 304:
            # 304 没有响应体，是"你那边的副本还有效"，不是空文件
            return Response(status_code=304, headers=headers)
        iterator = upstream_resp.aiter_raw()
        try:
            first_chunk = await iterator.__anext__()
        except StopAsyncIteration:
            first_chunk = b""
        if not first_chunk and upstream_resp.status_code < 400:
            log.warning("文件内容为空: path=%s status=%s upstream=%s",
                        path, upstream_resp.status_code, upstream[:160])
            raise HTTPException(
                status_code=502,
                detail="文件内容为空：源站返回 0 字节。该文件在源站可能为空或已损坏，"
                       "与预览服务无关（可先直接下载该文件确认大小）")

        async def _relay():
            if first_chunk:
                yield first_chunk
            async for chunk in iterator:
                yield chunk

        return StreamingResponse(_relay(),
                                 status_code=upstream_resp.status_code, headers=headers)
    return StreamingResponse(upstream_resp.aiter_raw(),
                             status_code=upstream_resp.status_code, headers=headers)


# ------------------------------------------------------------------ 管理
def _gen_secret() -> str:
    """生成强随机密钥: kk- + 32 位 URL 安全串"""
    import secrets
    return "kk-" + secrets.token_urlsafe(24)


class AppIn(BaseModel):
    appId: str
    appName: str = ""
    appSecret: str = ""          # 留空表示自动生成(新增)或保持不变(已存在)
    ipWhitelist: str = ""
    qpsLimit: int = Field(default=60)


@app.post("/api/v1/admin/app")
async def create_app(body: AppIn, request: Request, _=Depends(require_admin)):
    """新增或更新接入系统。appSecret 留空时自动生成。"""
    app_id = body.appId.strip()
    if not app_id:
        raise HTTPException(status_code=400, detail="appId 不能为空")
    exist = await db.query_one(
        "SELECT app_secret FROM app_account WHERE app_id=%s", (app_id,))
    if body.appSecret.strip():
        secret = body.appSecret.strip()
        generated = False
    elif exist:
        secret = exist["app_secret"]      # 更新其它字段时不覆盖原密钥
        generated = False
    else:
        secret = _gen_secret()
        generated = True
    await db.execute(
        "INSERT INTO app_account (app_id, app_name, app_secret, ip_whitelist, qps_limit)"
        " VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE app_name=%s, app_secret=%s,"
        " ip_whitelist=%s, qps_limit=%s, enabled=1",
        (app_id, body.appName, secret, body.ipWhitelist, body.qpsLimit,
         body.appName, secret, body.ipWhitelist, body.qpsLimit))
    return ok({"appId": app_id, "appSecret": secret, "generated": generated,
               "action": "updated" if exist else "created"})


@app.get("/api/v1/admin/apps")
async def list_apps(_=Depends(require_admin)):
    """列出所有接入系统(含密钥, 需管理员令牌)。"""
    return ok(await db.query(
        "SELECT app_id, app_name, app_secret, ip_whitelist, qps_limit, enabled,"
        " remark, created_at, updated_at FROM app_account ORDER BY created_at DESC"))


@app.post("/api/v1/admin/app/{app_id}/reset-secret")
async def reset_secret(app_id: str, _=Depends(require_admin)):
    secret = _gen_secret()
    await db.execute("UPDATE app_account SET app_secret=%s WHERE app_id=%s",
                     (secret, app_id))
    return ok({"appId": app_id, "appSecret": secret})


class QpsIn(BaseModel):
    qpsLimit: int = Field(ge=0, le=1000, description="0 = 不限（不建议）")


@app.post("/api/v1/admin/app/{app_id}/qps")
async def set_app_qps(app_id: str, body: QpsIn, _=Depends(require_admin)):
    """调整单个系统的 QPS 配额，不用改数据库。

    20 个系统共用一个 global 120 QPS，单个系统给多少要能随时调：
    批量归档的窗口期可以临时抬到 60，平时压到 20 保护其他人。
    """
    await db.execute("UPDATE app_account SET qps_limit=%s WHERE app_id=%s",
                     (body.qpsLimit, app_id))
    row = await db.query_one(
        "SELECT qps_limit FROM app_account WHERE app_id=%s", (app_id,))
    if not row:
        raise HTTPException(status_code=404, detail="没有这个系统: %s" % app_id)
    return ok({"appId": app_id, "qpsLimit": row["qps_limit"]})


@app.post("/api/v1/admin/app/{app_id}/toggle")
async def toggle_app(app_id: str, _=Depends(require_admin)):
    await db.execute(
        "UPDATE app_account SET enabled = IF(enabled=1, 0, 1) WHERE app_id=%s", (app_id,))
    row = await db.query_one("SELECT enabled FROM app_account WHERE app_id=%s", (app_id,))
    return ok({"appId": app_id, "enabled": row["enabled"] if row else 0})


@app.delete("/api/v1/admin/app/{app_id}")
async def delete_app(app_id: str, _=Depends(require_admin)):
    await db.execute("DELETE FROM app_account WHERE app_id=%s", (app_id,))
    return ok({"appId": app_id, "deleted": True})
# ------------------------------------------------------- 表格模式预览页 / PDF 降级
# 降级记忆(pdf_preferred / mark_pdf_preferred)统一放在 engine 里,
# 转码任务与预览页共用同一份逻辑。
@app.get("/stepview/{file_key}")
async def step_view_page(file_key: str, kkt: str = "", request: Request = None):
    """STEP/IGES 的 3D 预览（服务端预解析成网格，这里只负责画）。

    没有预解析产物时退回引擎的 O3DV 页面：那边是浏览器现场 WASM 解析，
    14MB 要 19.5 秒、47MB 要几分钟，体验差但没有网格时别无选择。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import step_view
    # 有线框产物（没有实体面 / 三角化跑不动的大装配体）也走这个页面，
    # 前端按文件头的 magic 决定画三角网格还是画线
    if not (step_view.mesh_ready(file_key) or step_view.wire_ready(file_key)):
        # 没有网格：去等待页（会入队补解析），不要丢给 /prog —— 那是 PPT 内容页
        return RedirectResponse("/stepwait/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    from . import sheet as _sheet
    body = step_view.viewer_html(file_key, engine.name_of(info.get("url", "")),
                                 kkt, _sheet.watermark_bg_url(),
                                 loadmetric.beacon(kkt, 60000))
    return HTMLResponse(body, headers={"Cache-Control": "no-store"})


@app.get("/stepempty/{file_key}")
async def step_empty_page(file_key: str, kkt: str = ""):
    """模型里没有实体面（预解析结果为空）时的结论页。

    这类文件（空装配体、只有曲线/曲面/基准面）交给 O3DV 也画不出东西，
    只会白解析几十秒再报 "The model doesn't contain any meshes."。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import step_view
    reason = step_view.empty_reason(file_key) or "预解析结果为空"
    return HTMLResponse(_empty_model_page(reason),
                        headers={"Cache-Control": "no-store"})


async def _read_source_head(url: str, cap: int):
    """读源文件前 cap 字节，返回 (bytes, 总大小)；读不到返回 (None, 0)。

    本地副本优先：业务接口（`/api/file/raw`）服务端 GET 是 403，受控文件却就在挂载上。
    """
    lp = engine.local_path_of(url)
    if lp:
        try:
            size = os.path.getsize(lp)
            with open(lp, "rb") as f:
                return f.read(cap), size
        except OSError:
            pass
    client = await engine.http()
    try:
        async with client.stream("GET", url, timeout=60) as r:
            if r.status_code >= 400:
                return None, 0
            total = int(r.headers.get("content-length") or 0)
            buf = b""
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= cap:
                    break
            return buf[:cap], max(total, len(buf))
    except Exception as exc:
        log.warning("文本预览读取失败(%s): %s", engine.file_key_of(url)[:12], exc)
        return None, 0


@app.get("/textview/{file_key}")
async def text_view_page(file_key: str, kkt: str = ""):
    """纯文本类（txt/json/log/源码…）的秒开页。

    以前交给引擎渲染，实测 txt 端到端均值 13.3 秒、最慢 153 秒，而文件只有几十 KB ——
    引擎为它起了一整套转换流程。网关自己读字节 + 猜编码 + `<pre>` 是毫秒级，
    页面复用压缩包内文本预览那一套（同样带水印）。
    """
    from . import archive_browse as ab, sheet
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    url = info.get("url", "")
    name = engine.name_of(url)
    cap = int(config.TEXT_DIRECT_MAX_MB * 1048576)
    data, total = await _read_source_head(url, cap)
    if data is None:
        raise HTTPException(status_code=502, detail="读取源文件失败（源站不可达或需要鉴权）")
    text = ab.decode_text(data)
    meta = "%d 个字符" % len(text)
    if total > len(data):
        meta += "（已截断，仅显示前 %.0f MB）" % config.TEXT_DIRECT_MAX_MB
    # 文本页以前没有可见性探针，看板上 txt 的"加载耗时"永远是空的（或只有接口
    # 那 0.4 秒）—— 用户确实在看，只是没人上报。探针 3 秒上限：文本是直出的，
    # 超过 3 秒还没画出来基本就是渲染卡住，不必再等。
    html = loadmetric.inject(_text_page(name, text, sheet.watermark_bg_url(), url, meta),
                             kkt, 3000)
    return HTMLResponse(html, headers={"Cache-Control": "private, max-age=60"})


async def _step_enqueue(fk: str, url: str) -> None:
    """把"缺网格的 STEP"推给宿主机 worker（tools/step_worker.py 消费）。

    SETNX 去重：同一个人反复刷新、或者多人同时打开，队列里只留一份。
    去重键 10 分钟过期，失败的文件过一会儿还能再试一次。
    """
    try:
        r = await db.get_redis()
        if await r.set("kk:step:q:%s" % fk, "1", nx=True, ex=600):
            await r.lpush("kk:step:need", "%s|%s" % (fk, url or ""))
            log.info("STEP 首次解析入队 %s", fk[:12])
    except Exception as exc:
        log.warning("STEP 入队失败 %s: %s", fk[:12], exc)


@app.get("/stepwait/{file_key}")
async def step_wait_page(file_key: str, kkt: str = ""):
    """首次加载的 STEP：还没有网格，正在让宿主机补解析。

    不能让浏览器去引擎现场 WASM 解析（17MB 几十秒、200MB 基本打不开），
    这里入队补网格，页面每 2 秒问一次状态，好了自动跳 /stepview。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import step_view
    if step_view.mesh_ready(file_key) or step_view.wire_ready(file_key):
        return RedirectResponse("/stepview/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    if step_view.empty_reason(file_key):
        return RedirectResponse("/stepempty/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    await _step_enqueue(file_key, info.get("url", ""))
    return HTMLResponse(_step_wait_page(file_key, kkt),
                        headers={"Cache-Control": "no-store"})


@app.get("/stepstate/{file_key}")
async def step_state(file_key: str, kkt: str = ""):
    """等待页轮询用：ready / empty / failed / running / queued。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import step_view
    if step_view.mesh_ready(file_key):
        return {"state": "ready", "mode": "mesh"}
    if step_view.wire_ready(file_key):
        # 线框态也要带解析进度，否则前端只能显示"实体网格解析中"干等，
        # 不知道到底转到第几个零件了（这里以前直接 return，prog 读不到）
        prog = ""
        try:
            r = await db.get_redis()
            prog = await r.get("kk:step:prog:%s" % file_key) or ""
        except Exception:
            pass
        # 转换已经跑完、确实没有实体面时给个明确标记，
        # 否则前端一直显示"实体网格解析中"，用户以为还在转
        return {"state": "ready", "mode": "wire", "prog": prog,
                "nomesh": step_view.no_solid(file_key)}
    if step_view.empty_reason(file_key):
        return {"state": "empty"}
    prog = ""
    try:
        r = await db.get_redis()
        st = await r.get("kk:step:state:%s" % file_key)
        prog = await r.get("kk:step:prog:%s" % file_key) or ""
    except Exception:
        st = None
    return {"state": st or "queued", "prog": prog}


@app.get("/stepdata/{file_key}")
async def step_data(file_key: str, request: Request, kkt: str = ""):
    """预解析好的网格（已 gzip 存盘，直接回字节，不再压一遍）。

    令牌优先从 X-Preview 头取：URL 里不带 token 才能让浏览器用同一份缓存
    （否则每次打开 token 不同，等于每次都重新下载 86MB 网格）。
    """
    kkt = kkt or request.headers.get("x-preview", "")
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import step_view
    p = step_view.mesh_path(file_key)
    if not os.path.exists(p) or os.path.getsize(p) < 1024:
        # 没有三角网格时回线框（W3DM 格式，前端按 magic 分流）
        p = step_view.wire_path(file_key)
    if not os.path.exists(p):
        raise HTTPException(status_code=404, detail="模型网格尚未生成")
    # gzip 的 ISIZE（最后 4 字节）就是解压后大小，给前端算下载进度用
    usize = ""
    try:
        with open(p, "rb") as fh:
            fh.seek(-4, 2)
            usize = str(int.from_bytes(fh.read(4), "little"))
    except OSError:
        pass
    cc = "private, max-age=0, must-revalidate"
    resp, etag = _maybe_304(request, p, cc)
    if resp is not None:
        log.info("stepdata 304 %s", file_key[:12])
        return resp
    hdrs = {"Content-Encoding": "gzip", "Cache-Control": cc}
    if etag:
        hdrs["ETag"] = etag
    if usize:
        hdrs["X-Uncompressed-Size"] = usize
    return FileResponse(p, media_type="application/octet-stream", headers=hdrs)



@app.get("/cadvec/{file_key}")
async def cad_vec_data(file_key: str, request: Request, kkt: str = ""):
    """DWG 的矢量图（SVG）。没有产物就入队让宿主机渲染，前端轮询等一下。

    和 /cadpng（引擎转 PDF 再栅格化的位图）并列：位图 1600px 且单色，
    矢量版保留 12 万实体的真实几何，缩放不糊、还能量尺寸。
    """
    kkt = kkt or request.headers.get("x-preview", "")
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import sheet as _sheet
    p = os.path.join(_sheet.SHEET_DIR, file_key + ".cad.svg.gz")
    # 阶段进度先读出来：往下不论走"直接回文件"还是"还在渲染"，都用得上。
    # 前端拿到 200 时也要能补上"生成矢量图 XX 秒"这一行，否则刚好在
    # 文件落盘的瞬间轮询，阶段列表会缺一段（实测就是这么漏的）。
    prog = None
    try:
        r0 = await db.get_redis()
        raw = await r0.get("kk:cad:vec:pg:" + file_key)
        if raw:
            prog = json.loads(raw)
    except Exception:
        prog = None

    if os.path.exists(p) and os.path.getsize(p) > 1024:
        cc = "private, max-age=300"
        resp, etag = _maybe_304(request, p, cc)
        if resp is not None:
            return resp
        hdrs = {"Content-Encoding": "gzip", "Cache-Control": cc}
        if etag:
            hdrs["ETag"] = etag
        if prog:
            hdrs["X-Cad-Prog"] = json.dumps(prog, ensure_ascii=True)
        return FileResponse(p, media_type="image/svg+xml", headers=hdrs)

    st = ""
    try:
        r = await db.get_redis()
        st = await r.get("kk:cad:vec:st:" + file_key) or ""
        if not st:
            if await r.set("kk:cad:vec:q:" + file_key, "1", nx=True, ex=300):
                await r.lpush("kk:cad:vec", "%s|%s" % (file_key, info.get("url", "")))
                log.info("CAD 矢量渲染入队 %s", file_key[:12])
    except Exception as exc:
        log.warning("CAD 入队失败 %s: %s", file_key[:12], exc)
    if st.startswith("fail:"):
        return JSONResponse({"state": "failed", "detail": st[5:], "prog": prog}, status_code=202)
    return JSONResponse({"state": "rendering", "prog": prog}, status_code=202)


@app.get("/cadv/{file_key}")
async def cad_vec_page(file_key: str, kkt: str = ""):
    """DWG 矢量预览页：缩放/平移/量尺寸。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    title = os.path.basename(unquote(info.get("url", ""))) or file_key[:12]
    html = (_CADV_PAGE.replace("__TITLE__", html_escape(title))
            .replace("__FK__", json.dumps(file_key))
            .replace("__KKT__", json.dumps(kkt)))
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/cadview/{file_key}")
async def cad_view_page(file_key: str, kkt: str = "", p: int = 0,
                        w: int = 0, request: Request = None):
    """CAD 位图查看器（DWG/DXF）。

    为什么不用 pdf.js：Aspose 生成的 PDF 里每条线段是一次独立描边，复杂图纸
    有 400 多万条、45MB，pdf.js 渲染 4 分钟都出不来；PDFium 渲染同页只要 0.8 秒。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import cad_view, sheet as _sheet
    if await _refresh_if_expired(file_key, info.get("url", ""), info.get("app", ""),
                                 info.get("emp", "")):
        return RedirectResponse("/prog/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    if not cad_view.pdf_ready(file_key):
        # PDF 还没落地：给等待页，转换完成后自动回到这里
        return HTMLResponse(_wait_page(file_key, quote(kkt, safe=""),
                                       _sheet.watermark_bg_url(),
                                       _wait_hint(info.get("url", ""))),
                            headers={"Cache-Control": "no-store"})
    pages = cad_view.pages_of(file_key)
    width = int(w) if w else 1600
    page = max(0, min(int(p), pages - 1))
    html_txt = cad_view.viewer_html(file_key, engine.name_of(info.get("url", "")),
                                    quote(kkt, safe=""), _sheet.watermark_bg_url(),
                                    width=width, page=page, pages=pages)
    # 位图 onload 才算"看到图纸"：CAD 的加载耗时从这里结算，不再用接口时间糊弄
    return HTMLResponse(loadmetric.inject(html_txt, kkt, 120000),
                        headers={"Cache-Control": "no-store"})


@app.get("/cadpng/{file_key}")
async def cad_png(file_key: str, kkt: str = "", w: int = 2400, p: int = 0):
    """CAD 图纸位图。按宽度分档缓存到磁盘，命中直接回文件。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import cad_view
    if not cad_view.pdf_ready(file_key):
        raise HTTPException(status_code=404, detail="图纸尚未转换完成")
    data = await cad_view.render_png(file_key, w, p)
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=1800"})


@app.get("/sheet/{file_key}")
async def sheet_page(file_key: str, kkt: str = "", simple: bool = False,
                     pdf: bool = False, fb: bool = False, x: bool = False,
                     request: Request = None):
    """表格模式预览页: 直接返回渲染好的 Excel/CSV 表格(不经过 PDF 转换)。

    pdf=1: 降级通道。表格组件异常时改由引擎转 PDF 后预览——引擎侧渲染稳定,
            不依赖前端组件, 代价是要等一次转码(通常 1~6 秒, 之后走缓存)。
    x=1:   展开模式。忽略 Excel 的隐藏行列与冻结窗格, 按原始数据全量显示。
            首次访问时由缓存的源文件现场生成, 之后复用。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    # 表格产物同样受缓存有效期约束: 过期就重转, 不拿 30 分钟前的 HTML 顶
    if await _refresh_if_expired(file_key, info.get("url", ""), info.get("app", ""),
                                 info.get("emp", "")):
        return RedirectResponse("/prog/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    if pdf:
        # fb=1 表示这是"组件异常后的自动/手动降级": 记住这个文件, 下次直接走 PDF,
        # 免得同一个文件每次都要卡一次才降级。按文件名记(URL 里的签名每次都变),
        # 7 天后自动失效, 届时再试表格模式。
        if fb:
            await engine.mark_pdf_preferred(engine.name_of(info.get("url", "")))
        # 引擎地址需二次编码, 抵消引擎的 URL 规范化(文件名含 & 时源站签名才对得上)
        return RedirectResponse(url="/kk/onlinePreview?url=%s&kkt=%s" % (
            engine.encode_url(engine.to_engine_url(info.get("url", ""))), kkt))
    if request:
        request.state.app_id = info.get("app", "")
        request.state.ev.update({"app_id": info.get("app", ""),
                                 "emp_no": info.get("emp", ""),
                                 "emp_name": info.get("nm", ""),
                                 "dept": info.get("dp", ""),
                                 "file_key": file_key,
                                 "file_url": info.get("url", ""),
                                 "file_name": engine.name_of(info.get("url", "")),
                                 "file_ext": engine.ext_of(info.get("url", "")),
                                 "action": "open", "result": "success"})
    from . import sheet
    if x:
        path = sheet.sheet_path(file_key, "expand")
        if not os.path.exists(path):
            # 展开版按需生成: 从缓存的源 xlsx 重渲染一次(约 0.6 秒), 之后复用。
            src = engine.src_xlsx_path(file_key)
            if not os.path.exists(src):
                raise HTTPException(status_code=404,
                                    detail="源文件已清理，无法展开，请重新打开该文件")
            from . import xlsx_html

            def _gen():
                with open(src, "rb") as fh:
                    raw = fh.read()
                return xlsx_html.build(raw, engine.name_of(info.get("url", "")),
                                       sheet.watermark_bg_url(), file_key, expand=True)

            page_html = await asyncio.get_event_loop().run_in_executor(None, _gen)
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(page_html)
            os.replace(tmp, path)       # 原子替换: 并发点击不会读到半截文件
    else:
        path = sheet.sheet_path(file_key, "simple" if simple else "")
    if not os.path.exists(path):
        # 渲染产物丢失(容器重启/缓存清理): 给明确指引, 而不是让 iframe 一直转圈或显示 404 白页
        return HTMLResponse(status_code=404, content="""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>请重新打开</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
background:#fff;font-family:'Microsoft YaHei',Arial,sans-serif;color:#222}
.box{text-align:center;max-width:560px;padding:24px;line-height:1.9}
h1{font-size:16px;margin:0 0 12px;font-weight:600}
p{font-size:13px;color:#5b636e;margin:0}</style></head><body>
<div class="box"><h1>该文件的渲染结果已失效</h1>
<p>服务重启或缓存清理导致渲染文件丢失。<br>
请返回业务系统<b>重新点击打开</b>该文件，系统会自动重新生成。</p></div>
</body></html>""")
    if os.path.getsize(path) < 6 * 1024 * 1024:
        # 加"内容可见即上报"脚本，让表格页的耗时也进加载耗时统计。
        # 超过 6MB 的表格页直接走 FileResponse：那种页面本身就是秒级瓶颈，
        # 为了一个统计字段整份读进内存不值。
        with open(path, encoding="utf-8", errors="replace") as fh:
            return HTMLResponse(loadmetric.inject(fh.read(), kkt, 120000),
                                headers={"Cache-Control": "private, max-age=300"})
    return FileResponse(path, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "private, max-age=300"})


@app.get("/pdfview/{file_key}")
async def pdf_view_page(file_key: str, kkt: str = "", native: int = 0,
                        request: Request = None):
    """本地转换出的 PDF 预览页。

    默认交给引擎的 pdf.js 查看器渲染 —— 和原生 PDF 预览完全一致(工具栏/缩放/水印/页码),
    只是文件流换成网关自己转出来的 PDF。
    native=1 时退回浏览器内置查看器(个别浏览器禁用 pdf.js 时排查用)。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import sheet as _sheet
    # 刷新页面时也要校验缓存时效: 过期就重新转换, 而不是继续显示旧 PDF
    if await _refresh_if_expired(file_key, info["url"], info.get("app", ""),
                                 info.get("emp", "")):
        return RedirectResponse("/prog/%s?kkt=%s" % (file_key, quote(kkt, safe="")))
    if not _sheet.local_pdf_ready(file_key):
        raise HTTPException(status_code=404, detail="PDF 尚未生成, 请稍后重试")
    if request is not None:
        request.state.app_id = info.get("app", "")
        request.state.ev.update({"app_id": info.get("app", ""),
                                 "emp_no": info.get("emp", ""),
                                 "emp_name": info.get("nm", ""),
                                 "dept": info.get("dp", ""),
                                 "file_key": file_key,
                                 "file_url": info.get("url", ""),
                                 "file_name": engine.name_of(info.get("url", "")),
                                 "file_ext": engine.ext_of(info.get("url", "")),
                                 "action": "open", "result": "success"})
    qkkt = quote(kkt, safe="")
    if not native:
        pdf_url = "%s/pdf/%s?kkt=%s" % (config.PUBLIC_BASE, file_key, qkkt)
        target = ("/kk/pdfjs/web/viewer.html?file=%s&kkt=%s"
                  "&disablepresentationmode=true&disableopenfile=true"
                  "&disableprint=false&disabledownload=true&disablebookmark=true"
                  "&_v=%s"
                  % (quote(pdf_url, safe=""), qkkt, config.ASSET_VER))
        # 大 PPT 两阶段：部分版的进度横幅由 pdf.js 页面注入时从 Redis 现取
        # （见 proxy_engine → inject_pdfjs），这里不再往 URL 上带 partial，
        # 避免用户刷新时 URL 里的旧参数把"已完成"的页面再次标成部分版。
        return RedirectResponse(url=target)

    wm = _sheet.watermark_bg_url()
    return HTMLResponse(content="""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PDF 预览</title>
<style>
html,body{margin:0;height:100%%;background:#fff;overflow:hidden}
#pdf{position:absolute;inset:0;width:100%%;height:100%%;border:0;background:#fff}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9999;
 background-image:url("%s");background-repeat:repeat}
.tip{position:fixed;left:0;right:0;bottom:0;padding:5px 12px;font-size:12px;color:#8a9099;
 background:rgba(255,255,255,.94);border-top:1px solid #eee;z-index:10000}
.tip a{color:#4a7cf7;text-decoration:none}
</style></head><body>
<iframe id="pdf" src="/pdf/%s?kkt=%s"></iframe>
<div id="wm"></div>
<div class="tip">内容显示异常？<a href="/pdf/%s?kkt=%s" target="_blank">在新窗口打开</a></div>
</body></html>""" % (wm, file_key, qkkt, file_key, qkkt))


@app.get("/pdf/{file_key}")
async def pdf_file(file_key: str, kkt: str = "", request: Request = None):
    """返回网关本地转换的 PDF 文件流(需预览令牌)。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import sheet as _sheet
    path = _sheet.local_pdf_path(file_key)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="PDF 不存在")
    return FileResponse(path, media_type="application/pdf",
                        headers={"Content-Disposition": "inline",
                                 # 必须不缓存: 两阶段补全 / 30 分钟过期重建都会替换这个文件,
                                 # 浏览器缓存住旧 PDF 就会"明明转好了还是 15 页"
                                 "Cache-Control": "no-store, must-revalidate"})


# ------------------------------------------------------------------ 压缩包内文件预览
# 清单页里的文件名可点开：图片/PDF/音视频直接出字节，xlsx 出原生表格，
# doc/ppt 用 LibreOffice 转 PDF，文本出等宽页，其余给下载。
# 压缩包本体缓存在 <key>.arc（转换任务生成清单时顺手落盘），点开条目时按需取。
def _arc_b64(names) -> str:
    raw = json.dumps(list(names), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _arc_chain(s: str):
    if not s:
        return []
    try:
        pad = "=" * (-len(s) % 4)
        v = json.loads(base64.urlsafe_b64decode(s + pad).decode("utf-8"))
        return [str(x) for x in v] if isinstance(v, list) else []
    except Exception:
        return []


async def _arc_pkg(file_key: str, url: str) -> str:
    """压缩包本体的本地路径（没有就现下载一份并缓存）。

    过期语义与清单页一致：先比对源指纹，没变就继续用；变了才重下。
    不这样做的话，包被替换后直接点条目（不经清单页）会一直读到旧字节。
    """
    p = engine.arc_cache_path(file_key)
    have = False
    try:
        if os.path.getsize(p) > 0:
            have = True
            # 用包自己的 mtime 判新鲜度，不看清单页(.html)的产物时间：
            # 用户可能从会话里直接点条目、从没打开过清单页，那时 .html 不存在，
            # 按产物判会永远"不过期"，包更新后一直读到旧字节。
            age = time.time() - os.path.getmtime(p)
            if config.CACHE_TTL_SECONDS <= 0 or age < config.CACHE_TTL_SECONDS:
                return p
            if await engine.revalidate(file_key, url):
                os.utime(p, None)          # 源没变，续期，别每分钟白问一次
                return p
    except OSError:
        pass
    try:
        client = await engine.http()
        r = await client.get(url, timeout=300)
        if r.status_code >= 400:
            raise RuntimeError("HTTP %s" % r.status_code)
        body = r.content
    except Exception as exc:
        if have:
            # 源暂时拿不到（签名过期/源站抽风）：宁可给旧包，也别让用户彻底打不开。
            # 与 _refresh_if_expired 里"源不可达就不删缓存"是同一条原则。
            log.warning("压缩包重新下载失败(%s)，继续用旧缓存: %s", file_key, exc)
            return p
        raise HTTPException(status_code=502, detail="下载压缩包失败: %s" % exc)
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    engine.trim_arc_cache()
    # ponytail: 临时名带随机后缀。原来固定 .part，两个请求同时点同一包的不同
    # 条目时会往同一个文件里交错写，落盘就是坏包。
    tmp = "%s.%s.part" % (p, uuid.uuid4().hex[:8])
    with open(tmp, "wb") as f:
        f.write(body)
    os.replace(tmp, p)
    return p


def _arc_drill(src, ext: str, names):
    """逐层进入嵌套压缩包，返回当前层的 (src, ext)。

    内层只有 zip/tar 能从内存读；rar/7z 需要落盘，遇到就抛 UnsupportedArchive。
    """
    from . import archive_browse as ab
    cur_src, cur_ext = src, ext
    for name in names:
        data = ab.read_entry(cur_src, cur_ext, name)
        cur_src, cur_ext = data, ab.ext_of_name(name)
    return cur_src, cur_ext


_TEXT_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>
html,body{margin:0;height:100%%;background:#fff}
body{font:13px/1.7 "Consolas","Courier New","Microsoft YaHei",monospace;color:#24292f}
.hd{position:sticky;top:0;z-index:5;background:#fff;border-bottom:1px solid #eef0f3;
 padding:8px 14px;display:flex;gap:12px;align-items:baseline;
 font-family:-apple-system,"Microsoft YaHei",sans-serif}
.hd b{font-weight:600;font-size:13px;word-break:break-all}
.hd .m{color:#8a9099;font-size:12px}
.hd a{margin-left:auto;color:#2f6feb;text-decoration:none;font-size:12px;white-space:nowrap}
pre{margin:0;padding:12px 14px;white-space:pre-wrap;word-break:break-word}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div class="hd"><b>%(title)s</b><span class="m">%(meta)s</span><a href="%(raw)s">下载原文件</a></div>
<pre>%(body)s</pre>
<div id="wm"></div></body></html>"""


def _text_page(name: str, text: str, wm_url: str, raw_url: str, meta: str) -> str:
    import html as _html
    return _TEXT_PAGE % {"title": _html.escape(name.rsplit("/", 1)[-1]),
                         "meta": _html.escape(meta),
                         "body": _html.escape(text),
                         "wm": wm_url, "raw": _html.escape(raw_url, quote=True)}


_DL_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%(title)s</title>
<style>html,body{height:100%%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:#fff;
 color:#5b636e;font:14px/1.9 -apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
.box{text-align:center;padding:0 24px}
h1{margin:0 0 10px;font-size:15px;font-weight:600;color:#222}
p{margin:0 0 14px;color:#8a9099;font-size:13px}
a.btn{display:inline-block;padding:7px 18px;border-radius:6px;background:#2f6feb;
 color:#fff;text-decoration:none;font-size:13px}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body><div class="box">
<h1>%(title)s</h1><p>%(msg)s</p><a class="btn" href="%(raw)s">下载原文件</a>
</div><div id="wm"></div></body></html>"""


async def _entry_to_pdf(file_key: str, name: str, data: bytes, ext: str) -> str:
    """压缩包里的 Office 文件 → 本地 PDF（按条目名缓存，重复点开不再转）。"""
    # 用内容摘要而不是长度：同名同长度但内容变了的文件不能命中旧 PDF
    tag = hashlib.md5(file_key.encode("utf-8") + b"|" + name.encode("utf-8")
                      + b"|" + hashlib.md5(data).digest()).hexdigest()[:16]
    out = os.path.join(_sheet_dir(), "%s.arc-%s.pdf" % (file_key, tag))
    try:
        if os.path.getsize(out) > 1024:
            return out
    except OSError:
        pass
    from . import office_pdf
    await office_pdf.to_pdf(data, ext, out)
    return out


async def _render_entry(file_key: str, name: str, data: bytes, raw_url: str):
    """按扩展名把一个条目渲染成能看的形式。"""
    from . import archive_browse as ab, sheet
    base = name.rsplit("/", 1)[-1]
    ext = ab.ext_of_name(name)
    disp = "inline; filename*=UTF-8''" + quote(base)

    if ext in ab.IMG_EXT:
        return Response(content=data, media_type=ab.safe_mime(base),
                        headers={"Content-Disposition": disp,
                                 "Cache-Control": "private, max-age=1800"})
    if ext == "pdf":
        return Response(content=data, media_type="application/pdf",
                        headers={"Content-Disposition": disp,
                                 "Cache-Control": "private, max-age=1800"})
    if ext in ab.AV_EXT:
        return Response(content=data, media_type=ab.safe_mime(base),
                        headers={"Content-Disposition": disp})

    # 文本类（含 csv/源码/日志）
    if ab.is_text(name):
        text = ab.decode_text(data[:3 * 1024 * 1024])
        truncated = len(data) > 3 * 1024 * 1024
        meta = "%d 个字符" % len(text) + ("（已截断，仅显示前 3MB）" if truncated else "")
        return HTMLResponse(_text_page(name, text, sheet.watermark_bg_url(), raw_url, meta))

    # 表格：原生 HTML（不受打印区域裁列），失败再转 PDF
    if ext in ab.OFFICE_SHEET:
        try:
            from . import xlsx_html
            return HTMLResponse(xlsx_html.build(data, base, sheet.watermark_bg_url(), ""))
        except Exception as exc:
            log.info("压缩包内表格按 HTML 渲染失败(%s), 改走 PDF: %s", base, exc)

    # Office 文档/演示/表格兜底：LibreOffice 转 PDF
    if ext in ab.OFFICE_PDF or ext in ab.OFFICE_SLIDE or ext in ab.OFFICE_SHEET:
        try:
            pdf = await _entry_to_pdf(file_key, name, data, ext)
            return FileResponse(pdf, media_type="application/pdf",
                                headers={"Content-Disposition": disp,
                                         "Cache-Control": "private, max-age=1800"})
        except Exception as exc:
            log.warning("压缩包内文件转 PDF 失败(%s): %s", base, exc)
            msg = "该文件无法在线预览（转换失败），可下载后用本地软件打开"
            return HTMLResponse(_DL_PAGE % {"title": html_escape(base), "msg": msg,
                                            "raw": html_escape(raw_url, quote=True),
                                            "wm": sheet.watermark_bg_url()})

    msg = "该格式暂不支持在线预览，可下载后打开"
    return HTMLResponse(_DL_PAGE % {"title": html_escape(base), "msg": msg,
                                    "raw": html_escape(raw_url, quote=True),
                                    "wm": sheet.watermark_bg_url()})


def html_escape(s: str, quote: bool = False) -> str:
    import html as _html
    return _html.escape(s or "", quote)


@app.get("/archive/{file_key}/view")
async def archive_view(file_key: str, p: str = "", chain: str = "", kkt: str = ""):
    """清单页里点一个文件：能渲染就渲染，是压缩包就进下一层。"""
    from . import archive_browse as ab, archive_list, sheet
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    url = info.get("url", "")
    qkkt = quote(kkt, safe="")
    layers = _arc_chain(chain)

    try:
        pkg = await _arc_pkg(file_key, url)
        src, ext = _arc_drill(pkg, engine.ext_of(url), layers)
    except ab.UnsupportedArchive as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except KeyError:
        raise HTTPException(status_code=404, detail="压缩包内找不到该文件")

    if p:
        try:
            data = ab.read_entry(src, ext, p)
        except KeyError:
            raise HTTPException(status_code=404, detail="压缩包内找不到该文件")
        except ab.UnsupportedArchive as exc:
            # 嵌套包里的 rar/7z 只能落盘读，内存里的字节读不了
            raise HTTPException(status_code=415, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc))
        # 目标还是压缩包 → 再进一层（URL 带上新 chain，便于"返回上级"）
        if ab.is_archive(p):
            nxt = _arc_b64(layers + [p])
            return RedirectResponse("/archive/%s/view?chain=%s&kkt=%s"
                                    % (file_key, nxt, qkkt))
        raw_url = "/archive/%s/raw?p=%s%s&kkt=%s" % (
            file_key, quote(p, safe=""),
            ("&chain=" + quote(chain, safe="")) if chain else "", qkkt)
        return await _render_entry(file_key, p, data, raw_url)

    # p 为空：列当前层的清单（顶层或某一层嵌套包）
    entries = ab.listing(src, ext)
    title = layers[-1].rsplit("/", 1)[-1] if layers else engine.name_of(url)
    back = _arc_b64(layers[:-1]) if layers else ""
    page = archive_list.build(entries, title, sheet.watermark_bg_url(), file_key,
                              back_chain=back)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/archive/{file_key}/raw")
async def archive_raw(file_key: str, p: str = "", chain: str = "", kkt: str = ""):
    """把压缩包里的一个条目按原样下载下来。"""
    from . import archive_browse as ab
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    if not p:
        raise HTTPException(status_code=400, detail="缺少文件名")
    layers = _arc_chain(chain)
    try:
        pkg = await _arc_pkg(file_key, info.get("url", ""))
        src, ext = _arc_drill(pkg, engine.ext_of(info.get("url", "")), layers)
        data = ab.read_entry(src, ext, p)
    except ab.UnsupportedArchive as exc:
        raise HTTPException(status_code=415, detail=str(exc))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    base = p.rsplit("/", 1)[-1]
    return Response(content=data, media_type=ab.safe_mime(base), headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(base),
        "Content-Length": str(len(data)),
    })


# 能做"快速内容页"的扩展名(真正解析的是 OOXML; 老版二进制先升级成 pptx)
PROG_EXTS = ("pptx", "ppt", "pps", "ppsx", "pptm", "ppsm", "potx", "pot",
             "odp", "dps")


def _wait_hint(file_url: str) -> str:
    """等待页上的一行预期说明。

    20 秒的转换没法省（Aspose 逐条线段描边），但"知道还要多久"和"不知道在等什么"
    是两种体验。CAD 尤其需要这句话——用户会以为是服务卡死了。
    """
    ext = engine.ext_of(file_url)
    if ext in config.CAD_EXTS:
        return "首次转换要十几秒（图纸越复杂越久），转好后再次打开是秒开。"
    if ext in ("ppt", "pptx", "doc", "docx", "xls", "xlsx"):
        return "大文件首次转换需要十几秒，转好后再次打开走缓存。"
    return ""


def _wait_page(file_key: str, qkkt: str, wm_url: str, hint: str = "") -> str:
    """转码等待页：内容页暂时给不出时用，绝不把 5xx 甩给用户。

    以前这里是 `raise HTTPException(503, "内容页生成失败: File is not a zip file")`，
    用户打开 .ppt 只看到一片 JSON 报错。现在改成轮询 + 自动切换：
    PDF 一就绪，本页 reload，/prog 会 302 到正式查看器。
    失败原因只写日志，不往用户脸上糊 "not a zip file" 这种内部细节。
    """
    return """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>正在生成预览…</title>
<style>
*{box-sizing:border-box}
body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
 background:#fff;color:#222;font-family:-apple-system,"Microsoft YaHei","PingFang SC",sans-serif}
.box{text-align:center;padding:0 24px}
.sp{width:34px;height:34px;margin:0 auto 18px;border:3px solid #e6e8eb;
 border-top-color:#4a7cf7;border-radius:50%%;animation:r .9s linear infinite}
@keyframes r{to{transform:rotate(360deg)}}
h1{margin:0 0 10px;font-size:16px;font-weight:600}
p{margin:0;color:#8a9099;font-size:13px;line-height:1.8}
.hint{color:#8a9099;font-size:12px;margin-top:10px}
.steps{margin:20px auto 0;min-width:302px;text-align:left}
.st{display:flex;gap:10px;align-items:center;font-size:12.5px;color:#8a9099;padding:5px 0}
.st b{flex:none;width:72px;font-weight:600;color:#9aa1ab}
.st.done b{color:#22a06b}
.st.done b::after{content:' ✓'}
.st.run b{color:#4a7cf7}
.st.run b::after{content:'…'}
.st i{font-style:normal;flex:none}
.st .bar{flex:1;height:4px;background:#e9ecef;border-radius:2px;overflow:hidden;min-width:80px}
.st .fill{display:none}
.st.run .fill{display:block;height:100%%;width:40%%;background:#4a7cf7;border-radius:2px;
 animation:sl 1.3s ease-in-out infinite}
@keyframes sl{0%%{margin-left:-40%%}50%%{margin-left:100%%}100%%{margin-left:-40%%}}
.el{color:#5b636e}
#wm{position:fixed;inset:0;pointer-events:none;z-index:9;
 background-image:url("%(wm)s");background-repeat:repeat}
</style></head><body>
<div class="box">
  <div class="sp"></div>
  <h1>正在生成预览…</h1>
  <div class="steps">
    <div class="st done"><b>校验文件</b><i>源文件可访问</i><div class="bar"></div></div>
    <div class="st run"><b>转换排版</b><i id="el2">0.0s</i><div class="bar"><div class="fill"></div></div></div>
    <div class="st"><b>生成页面</b><i>等待中</i><div class="bar"></div></div>
  </div>
  <p class="hint">%(hint)s</p>
</div>
<div id="wm"></div>
<script>
(function(){
  var t0=Date.now(), el=document.getElementById('el2');
  setInterval(function(){
    if(el) el.textContent=((Date.now()-t0)/1000).toFixed(1)+'s';
  },200);
  var n=0;
  var timer=setInterval(function(){
    if(++n>240){clearInterval(timer);return;}          /* 6 分钟放弃轮询 */
    fetch('/api/v1/preview/ready?f=%(fk)s&kkt=%(kkt)s',{cache:'no-store'})
      .then(function(r){return r.json()})
      .then(function(d){ if(d&&d.ready){ location.reload(); } })
      .catch(function(){});
  },1500);
})();
</script>
</body></html>""" % {"wm": wm_url, "fk": file_key, "kkt": qkkt, "hint": hint}


@app.get("/prog/{file_key}")
async def prog_page(file_key: str, kkt: str = "", request: Request = None):
    """PPT 快速内容页: 还没转好时先给"文字+配图", PDF 就绪后自动跳到正式查看器。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import sheet as _sheet
    qkkt = quote(kkt, safe="")
    _ext = engine.ext_of(info.get("url", ""))
    # 加密文件在等待页只会对着"正在转换"转圈到轮询超时（6 分钟），
    # 从旧链接进来时也要立刻给出结论。
    _fail = await engine.recent_failure(file_key)
    if not _fail:
        _enc = await _source_encryption(info.get("url", ""), _ext)
        if _enc:
            _fail = "ENC:" + _enc
            await engine.mark_failed(file_key, _fail, ttl=1800)
    if _fail.startswith("ENC:"):
        return HTMLResponse(_encrypted_page(_fail[4:]))
    mode = await engine.ready_mode(file_key)
    if _ext in config.STEP_EXTS:
        from . import step_view
        if step_view.mesh_ready(file_key) or step_view.wire_ready(file_key):
            return RedirectResponse("/stepview/%s?kkt=%s" % (file_key, qkkt))
    if mode == "" and _sheet.local_pdf_ready(file_key):
        mode = "pdf_local"          # 文件已转好, 只是标记丢了
    if mode == "pdf_local" and _sheet.local_pdf_ready(file_key):
        return RedirectResponse("/pdfview/%s?kkt=%s" % (file_key, qkkt))
    if mode == "sheet" and (_sheet.sheet_ready(file_key)
                            or _sheet.snap_ready(file_key)):
        return RedirectResponse("/sheet/%s?kkt=%s" % (file_key, qkkt))
    if mode == "cad":
        from . import cad_view
        if cad_view.pdf_ready(file_key):
            return RedirectResponse("/cadview/%s?kkt=%s" % (file_key, qkkt))
    if mode == "pdf":
        return RedirectResponse("/kk/onlinePreview?url=%s&kkt=%s" % (
            engine.encode_url(engine.to_engine_url(info["url"])), qkkt))
    if engine.ext_of(info.get("url", "")) not in PROG_EXTS:
        # 非演示文稿: 需要转换的类型也给等待页(缓存过期重建、Office 重转都会走到这里),
        # 免转码类型(PDF/图片/文本)没有等待的必要。
        ext0 = engine.ext_of(info.get("url", ""))
        if ext0 in config.CONVERT_EXTS or (config.SVG_PREVIEW and ext0 in config.SVG_EXTS):
            return HTMLResponse(_wait_page(file_key, qkkt,
                                           _sheet.watermark_bg_url(),
                                           _wait_hint(info.get("url", ""))),
                                headers={"Cache-Control": "no-store"})
        raise HTTPException(status_code=404, detail="该文件没有快速内容页")
    try:
        path = await engine.ensure_outline(info["url"], file_key,
                                           engine.name_of(info["url"]))
    except Exception as exc:
        # 内容页只是"提前看一眼"的加速手段, 它失败不代表文件打不开:
        # 后台 PDF 转换还在跑, 给等待页让用户等一会儿, 别甩 503。
        log.warning("内容页生成失败(%s), 已改为等待页: %s", file_key, exc)
        return HTMLResponse(_wait_page(file_key, qkkt,
                                       _sheet.watermark_bg_url(),
                                       _wait_hint(info.get("url", ""))),
                            headers={"Cache-Control": "no-store"})
    with open(path, encoding="utf-8") as f:
        page = f.read()
    page = page.replace("__KKT__", qkkt).replace("__WM__", _sheet.watermark_bg_url())
    return HTMLResponse(page)


@app.get("/progmedia/{file_key}/{name}")
async def prog_media(file_key: str, name: str, kkt: str = "", request: Request = None):
    """快速内容页里的图片: 按需从缓存的 pptx 里读, 不单独解压。"""
    token = kkt or (_extract_preview_token(request) if request is not None else "")
    if not token:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(token)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    path = engine.src_cache_path(file_key)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="源文件未缓存")
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("ppt/media/" + os.path.basename(name))
    except Exception:
        raise HTTPException(status_code=404, detail="图片不存在")
    ext = os.path.splitext(name)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".svg": "image/svg+xml",
            ".emf": "image/emf", ".wmf": "image/wmf", ".wdp": "image/vnd.ms-photo"
            }.get(ext, "application/octet-stream")
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/v1/preview/ready")
async def preview_ready(f: str, kkt: str = ""):
    """快速内容页轮询用: PDF 是否已就绪。"""
    info = security.parse_preview_token(kkt)
    if info.get("fk") != f:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    return {"ready": await engine.is_ready(f)}


@app.post("/api/v1/preview/loaded")
async def preview_loaded(request: Request):
    """预览页上报"首屏内容真正可见"的时刻（sendBeacon 发出，无自定义请求头）。

    这是看板上**加载耗时**的数据来源：
        加载耗时 = 本请求到达时刻 - /api/v1/preview/url 的申请时刻

    之所以不在这里做鉴权失败即报错：这是页面后台发的，报错了用户也看不到，
    直接静默返回即可（令牌签名照验，验不过就不记）。
    """
    try:
        raw = await request.body()
        data = json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return ok({"skipped": "bad-body"})
    kkt = (data.get("kkt") or "").strip()
    if not kkt:
        return ok({"skipped": "no-token"})
    try:
        info = security.parse_preview_token(kkt)
    except HTTPException:
        return ok({"skipped": "bad-token"})
    rid = info.get("rid") or request.headers.get("X-Request-Id", "")
    # bg=1：等待期间页面被切到后台过，浏览器节流会让"可见时刻"偏大，不计入统计
    return ok(await loadmetric.report(
        rid, data.get("phase") or "visible", bool(data.get("bg"))))


@app.get("/api/v1/preview/partial")
async def preview_partial(f: str, kkt: str = ""):
    """PDF 查看器轮询用: 部分版进度 + PDF 版本号 + 缓存时效。

    ver 是 PDF 文件的 mtime（秒）。它一变就说明后台换过文件
    （补全完整版 / 缓存过期重建），页面据此自动刷新，用户不必手动按 F5。
    """
    info = security.parse_preview_token(kkt)
    if info.get("fk") != f:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    age = engine.cache_age(f)
    return {"partial": await engine.partial_of(f),
            "ready": await engine.is_ready(f),
            "ver": engine.pdf_ver(f),
            "cacheAgeSeconds": int(age) if age >= 0 else -1,
            "cacheTtlSeconds": config.CACHE_TTL_SECONDS}


@app.get("/xlsmedia/{file_key}/{name}")
async def xls_media(file_key: str, name: str, request: Request = None):
    """Excel 表格页里的浮动图片: 按需从缓存的 xlsx 里读。

    令牌可能来自 URL(kkt)、Referer 或 Cookie——图片请求由浏览器发出,
    最稳的是页面脚本把 kkt 拼进 src, 这里三种都接受。
    """
    token = _extract_preview_token(request) if request is not None else ""
    if not token:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(token)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    path = engine.src_xlsx_path(file_key)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="源文件未缓存")
    import zipfile
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read("xl/media/" + os.path.basename(name))
    except Exception:
        raise HTTPException(status_code=404, detail="图片不存在")
    ext = os.path.splitext(name)[1].lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".bmp": "image/bmp", ".svg": "image/svg+xml"
            }.get(ext, "application/octet-stream")
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


async def _runtime_stats() -> dict:
    """转换队列深度、缓存占用、磁盘余量：health 与 /metrics 共用。"""
    from . import sheet
    row = await db.query_one(
        "SELECT SUM(status='queued') q, SUM(status='converting') c"
        " FROM preview_task WHERE updated_at > NOW() - INTERVAL 15 MINUTE")
    free = metrics.disk_free_bytes(sheet.SHEET_DIR)
    return {
        "converting": int((row or {}).get("c") or 0),
        "queued": int((row or {}).get("q") or 0),
        "cache_bytes": metrics.cache_dir_bytes(sheet.SHEET_DIR),
        "disk_free_bytes": free,
    }


def _file_etag(p):
    """按 mtime+size 生成 ETag。

    Starlette 的 FileResponse 会自带一个 etag，但**不处理 If-None-Match**，
    实测带上 If-None-Match 仍然回 200 整份内容。所以这里自己算、自己返回 304。
    """
    st = os.stat(p)
    return '"%x-%x"' % (int(st.st_mtime), st.st_size)


def _maybe_304(request, p, cache_control):
    """命中协商缓存就返回 304（几十字节），否则 None。"""
    try:
        etag = _file_etag(p)
    except OSError:
        return None, None
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": cache_control}), etag
    return None, etag


# 测速用的随机字节块：一次生成，避免每次请求都耗 CPU。
# 必须是随机数据 —— 全零会被 gzip 压到几百字节，测出来的是压缩速度不是带宽。
_NETPROBE_BLOCK = os.urandom(16 * 1024 * 1024)


@app.get("/api/v1/netprobe")
async def netprobe(size: int = 8388608):
    """给 3D 页面的"测速"按钮用：返回一段不可压缩的随机字节。"""
    n = max(64 * 1024, min(int(size or 0), len(_NETPROBE_BLOCK)))
    return Response(content=_NETPROBE_BLOCK[:n],
                    media_type="application/octet-stream",
                    headers={"Cache-Control": "no-store", "X-Probe-Bytes": str(n)})


@app.get("/health")
async def health():
    from . import archive_browse
    stat = await _runtime_stats()
    return {"status": "up", "service": config.SERVICE_NAME,
            "engine": config.KK_BASE, "time": datetime.now().isoformat(),
            # rar/7z 压缩包能否在线展开（缺 7zz 时自动退回引擎的只读清单）
            "archive7z": bool(archive_browse.sevenzip()),
            "converting": stat["converting"], "queued": stat["queued"],
            "cacheMB": round(stat["cache_bytes"] / 1048576.0, 1),
            "diskFreeGB": round(stat["disk_free_bytes"] / 1073741824.0, 1),
            # 当前未恢复的服务告警数（预览失败原因不在这里，见 /stats/failures）
            "alerts": await alerts.snapshot()}


@app.get("/metrics")
async def prometheus_metrics(request: Request):
    """Prometheus 抓取点。只允许内网/回环访问（指标里含主机与路径信息）。"""
    ip = security.client_ip(request)
    if not security.is_private_ip(ip):
        raise HTTPException(status_code=403, detail="指标仅限内网访问")
    stat = await _runtime_stats()
    body = metrics.render({
        "converting": stat["converting"],
        "queued": stat["queued"],
        "cache_bytes": stat["cache_bytes"],
        "disk_free_bytes": stat["disk_free_bytes"],
        "workers": 2,
    })
    return Response(content=body, media_type="text/plain; version=0.0.4")


app.include_router(stats_router)

# 前端页面(与 API 同进程同容器)
# ------------------------------------------------------------------ Univer 预览
# xlsx/xls 走 Univer Sheets（真正的单元格/公式/样式），
# xlsx/xls 走 Univer Sheets。快照由网关自己生成：Univer 1.1.0 的官方文件导入在
# @univerjs-pro（需商业授权），免费 Web SDK 只吃 snapshot。
def _univ_src_path(file_key: str) -> str:
    return os.path.join(_sheet_dir(), (file_key or "") + ".univsrc")


def _univ_snap_path(file_key: str) -> str:
    """表格快照的落盘缓存。

    快照是 openpyxl 现算出来的（real.xlsx 0.29s、APQP 周会表 1.0s），
    每次打开都重算纯属浪费。缓存跟着 invalidate 一起删，源文件改了自然会重建。
    """
    return os.path.join(_sheet_dir(), (file_key or "") + ".univsnap.json")


def _univ_page_png(file_key: str, page: int, width: int) -> str:
    return os.path.join(_sheet_dir(), "%s.upt_p%d_w%d.png" % (file_key or "", page, width))


def _sheet_dir() -> str:
    from . import sheet as _s
    os.makedirs(_s.SHEET_DIR, exist_ok=True)
    return _s.SHEET_DIR


async def _univ_source(file_key: str, file_url: str) -> bytes:
    """下载并缓存源文件（30 分钟有效期内复用）。

    **先读 `.src.xlsx`**：表格渲染时已经把同一份源文件落过盘（给单元格内嵌图片用），
    这里再存一份 `.univsrc` 纯属重复 —— 线上 680 个文件存了两份、多占 1.9G。
    顺序改成先读它之后，这些文件不会再生成新的 `.univsrc`。
    """
    for p in (engine.src_xlsx_path(file_key), _univ_src_path(file_key)):
        try:
            if os.path.getsize(p) > 0:
                with open(p, "rb") as f:
                    return f.read()
        except OSError:
            pass
    p = _univ_src_path(file_key)      # 下载后的落点（上面的循环变量不留给这里用）
    client = await engine.http()
    r = await client.get(file_url, timeout=300)
    if r.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail="下载源文件失败 HTTP %s" % r.status_code)
    tmp = p + ".part"
    with open(tmp, "wb") as f:
        f.write(r.content)
    os.replace(tmp, p)
    return r.content


def _univ_ppt_pdf_path(file_key: str) -> str:
    """幻灯片 PDF 的备用位置（早期版本转过就留在这里，仍要复用）。"""
    return os.path.join(_sheet_dir(), (file_key or "") + ".ppt.pdf")


def _slide_page_size(infos, vw: int, vh: int):
    """按容器尺寸算出幻灯片页的像素宽高，保证整页可见。

    Univer 的幻灯片画布不做缩放：页面 1200px 就占 1200px，超出画布直接裁。
    所以这里按容器（扣掉左右留白与顶部工具条）反算一个缩放比例，
    宽高两个方向取更保守的那个——宁可四周多点白边，也不要切掉内容。
    页与页之间比例可能不同（少数 PPT 混排 4:3 与 16:9），统一按第一页比例，
    否则 Univer 的 pageSize 只有一个，会导致后页错位。
    """
    w_pt, h_pt = infos[0]
    w_pt = max(1.0, float(w_pt))
    h_pt = max(1.0, float(h_pt))
    vw = int(vw or 1440)
    vh = int(vh or 900)
    avail_w = max(320, vw - 2 * config.UNIV_SLIDE_PAD_X)
    # 垂直方向是**对称**的：页面在画布里居中，两侧留白各占一半。
    # 所以扣 2*PAD_Y 才能保证"顶部留白 = PAD_Y"；原来扣 (48+8) 但只居中，
    # 顶部实际只剩 8px，工具条一挤就盖住页面标题。
    avail_h = max(240, vh - 2 * config.UNIV_SLIDE_PAD_Y
                  - config.UNIV_SLIDE_TOP_RESERVE)
    scale = min(avail_w / w_pt, avail_h / h_pt)
    return max(320, int(round(w_pt * scale))), max(180, int(round(h_pt * scale)))


async def _univ_slide_pdf(file_key: str, file_url: str, ext: str) -> str:
    """PPT/PPTX → 本地 PDF（Univer Slides 的每页图从这里渲染）。

    转换任务（engine.convert_task）已经把 PDF 写到 <key>.pdf，正常情况直接复用；
    只有在缓存被清、但页面还没被刷新时才需要在这里补转一次。
    """
    from . import sheet as _s
    for p in (_s.local_pdf_path(file_key), _univ_ppt_pdf_path(file_key)):
        try:
            if os.path.getsize(p) > 1024:
                return p
        except OSError:
            continue
    out = _s.local_pdf_path(file_key)
    from . import office_pdf
    data = await _univ_source(file_key, file_url)
    try:
        await office_pdf.to_pdf(data, ext, out)
        return out
    except Exception as exc:
        if ext not in ("ppt", "pps", "pot"):
            raise
        # 老版二进制 .ppt 直接转偶尔失败，先升级成 pptx 再转
        log.warning("直接转 .%s 失败, 改为先升级 pptx 再转(%s): %s", ext, file_key, exc)
        up = out + ".src.pptx"
        await office_pdf.to_ooxml(data, ext, up, "pptx")
        with open(up, "rb") as f:
            data2 = f.read()
        await office_pdf.to_pdf(data2, "pptx", out)
        return out


@app.get("/univer/{file_key}")
async def univer_view(file_key: str, kkt: str = "", request: Request = None):
    """Univer 预览页外壳（前端产物在 static/univer）。"""
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "static", "univer", "index.html")
    if not os.path.exists(path):
        path = os.path.join("static", "univer", "index.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=503,
                            detail="Univer 前端未构建（univer-preview/npm run build）")
    with open(path, encoding="utf-8") as f:
        page = f.read()
    # 水印：index.html 里只定义了 #wm 的样式，元素和背景图在这里注入。
    # 之前漏了这一步，导致 Univer 通道（xlsx/xls/pptx）**整页没有水印**——
    # 用户看到"只有前面第一个有"，其实是等待页(/prog)有水印，进了 Univer 就没了。
    # data URI 在 watermark_bg_url 里已整段 URL 编码，不含引号，可以安全放进 style。
    from . import sheet as _sheet
    wm = _sheet.watermark_bg_url()
    if wm and '<div id="app"></div>' in page:
        page = page.replace(
            '<div id="app"></div>',
            '<div id="app"></div>'
            '<style>#wm{background-image:url("%s")}</style>'
            '<div id="wm"></div>' % wm, 1)
    # Univer 表格/幻灯片是异步渲染的（快照要等接口回来），所以只能靠页面自己轮询画布
    return HTMLResponse(loadmetric.inject(page, kkt, 120000),
                        headers={"Cache-Control": "no-store"})


async def _lite_table(fk: str, url: str, name: str, kkt: str):
    """大表格 → 本地 PDF（/pdfview）。

    这类表（格式刷到 XFD 列）三条路实测对比，同一份 1.6MB 的《ZL-F32 质量门评审表》：
      Univer 快照（openpyxl 按 dimension 建对象）  28 秒
      xlsx_html 轻量视图（8.9MB HTML + 11 万个 <col>）  生成 2.1s + 浏览器渲染 65 秒
      LibreOffice → PDF                              3.0 秒 / 8 页 / 650KB
    PDF 是唯一能接受的，转好之后走缓存。
    """
    from . import office_pdf, sheet as _s
    out = _s.local_pdf_path(fk)
    try:
        ready = os.path.getsize(out) > 1024
    except OSError:
        ready = False
    if not ready:
        try:
            raw = await _univ_source(fk, url)
            await office_pdf.to_pdf(raw, "xlsx", out)
        except Exception as exc:
            log.warning("大表格转 PDF 失败(%s): %s", fk, exc)
            raise HTTPException(status_code=500,
                                detail="表格过于复杂，转换失败: %s" % exc)
    return ok({"kind": "redirect",
               "url": "/pdfview/%s?kkt=%s" % (fk, quote(kkt, safe=""))})


async def _sheet_snapshot(ud, raw: bytes, name: str):
    """openpyxl 建对象是纯 CPU 活，丢线程池并设上限。

    直接在事件循环里跑的话，一个 28 秒的大表会把整个 worker 卡住，
    同时进来的其它预览全部排队。
    """
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, ud.xlsx_to_workbook, raw, name),
        timeout=config.SHEET_SNAPSHOT_TIMEOUT)


@app.get("/api/v1/univer/data")
async def univer_data_api(kkt: str = "", vw: int = 0, vh: int = 0):
    """返回 Univer 快照：表格 → IWorkbookData，幻灯片 → ISlideData。

    vw/vh 是前端容器的 CSS 像素尺寸，只有幻灯片用：Univer 的幻灯片画布是
    1:1 画像素的，页面尺寸给大了就会被画布裁掉，给小了浪费空间。
    所以由浏览器量好容器再让服务端算页面尺寸，两边用同一把尺子。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    fk, url = info.get("fk", ""), info.get("url", "")
    ext = engine.ext_of(url)
    name = engine.name_of(url)
    if await _refresh_if_expired(fk, url, info.get("app", ""), info.get("emp", "")):
        raise HTTPException(status_code=409, detail="缓存已过期，请重新打开文件")
    from . import univer_data as ud
    t0 = time.time()
    try:
        if ext in config.UNIVER_SLIDE_EXTS:
            pdf = await _univ_slide_pdf(fk, url, ext)
            infos = ud.pdf_page_info(pdf)
            pw, ph = _slide_page_size(infos, vw, vh)
            qkkt = quote(kkt, safe="")
            rw = config.UNIV_SLIDE_RENDER_W
            pages = [{"width": pw, "height": ph,
                      "src": "/api/v1/univer/page/%s?kkt=%s&p=%d&w=%d"
                             % (fk, qkkt, i, rw)}
                     for i in range(len(infos))]
            data = ud.slides_snapshot(pages, name)
            kind = "slides"
        else:
            raw = await _univ_source(fk, url)
            if ext == "xls":
                from . import office_pdf
                up = _univ_src_path(fk) + ".xlsx"
                await office_pdf.to_ooxml(raw, "xls", up, "xlsx")
                with open(up, "rb") as f:
                    raw = f.read()
            # 快照优先：只要预热/上次已经生成过，就直接用，不再看 dimension。
            # 原来的顺序是先判 heavy 再读快照，于是"判大但实际不大"的表永远进不了
            # Univer，哪怕快照就躺在磁盘上。
            snap_p = _univ_snap_path(fk)
            data = None
            try:
                if os.path.getsize(snap_p) > 0:
                    with open(snap_p, encoding="utf-8") as fh:
                        data = json.load(fh)
            except (OSError, ValueError):
                data = None
            if data is None:
                why = ud.heavy_reason(raw)
                try:
                    if why:
                        # dimension 经常被格式刷到 XFD（实测 16384 列的表实际只有
                        # 2 万格、解析 0.4 秒）。给一个短窗口试快照，真解析不动才退
                        # PDF —— 否则用户看到的是一份不能选中/筛选的版面。
                        data = await asyncio.wait_for(
                            _sheet_snapshot(ud, raw, name),
                            timeout=config.UNIV_SNAP_TRY_TIMEOUT)
                    else:
                        data = await _sheet_snapshot(ud, raw, name)
                except asyncio.TimeoutError:
                    log.warning("快照超时改走 PDF(%s, %s)", fk,
                                why or "%ds" % config.SHEET_SNAPSHOT_TIMEOUT)
                    return await _lite_table(fk, url, name, kkt)
                tmp = snap_p + ".part"
                try:
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, ensure_ascii=False)
                    os.replace(tmp, snap_p)
                except OSError:
                    pass
            kind = "sheets"
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Univer 快照生成失败(%s): %s", fk, exc)
        # 标记下来：下一个打开的人直接走静态表格页，不必再白等一次空白
        await engine.mark_nosnap(fk, exc)
        raise HTTPException(status_code=500, detail="快照生成失败: %s" % exc)
    log.info("Univer 快照(%s) %s %.2fs", fk, kind, time.time() - t0)
    extra = {}
    if kind == "slides":
        # 大 PPT 两阶段：此时 PDF 可能只有前 N 页，页面要自己轮询完整版
        part = await engine.partial_of(fk)
        if part:
            extra["partial"] = part
    return ok({"kind": kind, "fileName": name, "data": data, **extra})


@app.get("/api/v1/univer/page/{file_key}")
async def univer_page(file_key: str, kkt: str = "", p: int = 0, w: int = 0):
    """PPT 某一页的位图（Univer Slides 的页面内容就是这张图）。

    按页独立请求：首屏只要第 1 页，剩下的等用户翻到再取，
    比一次性把几十页全渲出来快一个数量级。
    """
    if not kkt:
        raise HTTPException(status_code=401, detail="缺少预览令牌")
    info = security.parse_preview_token(kkt)
    if info.get("fk") != file_key:
        raise HTTPException(status_code=401, detail="预览令牌与文件不匹配")
    from . import sheet as _s
    from . import univer_data as ud
    pdf = _s.local_pdf_path(file_key)
    if not os.path.exists(pdf):
        alt = _univ_ppt_pdf_path(file_key)
        pdf = alt if os.path.exists(alt) else pdf
    if not os.path.exists(pdf):
        raise HTTPException(status_code=404, detail="幻灯片尚未转换完成")
    wide = max(400, min(int(w or config.UNIV_SLIDE_RENDER_W), 2400))
    page = max(0, int(p))
    data = await ud.render_page_jpg(
        pdf, page, wide, _univ_page_png(file_key, page, wide).replace(".png", ".jpg"))
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=1800"})


app.mount("/", StaticFiles(directory="static", html=True), name="static")
