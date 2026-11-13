import csv
import datetime
import io
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from . import config, db, engine, loadmetric
from .security import can_all, get_viewer, require_admin, viewer_scope

router = APIRouter(prefix="/api/v1", tags=["统计"])


@router.get("/health/detail")
async def health_detail(request: Request = None,
                        account: dict = Depends(get_viewer)):
    """四个服务的实时健康状态: 网关 / 预览引擎 / MySQL / Redis。"""
    if request:
        request.state.app_id = account["app_id"]
    services = []

    def _ms(t0):
        return int((time.perf_counter() - t0) * 1000)

    # 1. 网关自身
    services.append({"key": "gateway", "name": "API 网关", "status": "up",
                     "latencyMs": 0, "detail": "uvicorn · 端口 %s" % config.PORT})

    # 2. 预览引擎
    t0 = time.perf_counter()
    try:
        client = await engine.http()
        resp = await client.get(config.KK_BASE + "/", timeout=8)
        services.append({"key": "kkfileview", "name": "自研预览引擎（内核：kkFileView）",
                         "status": "up" if resp.status_code < 400 else "down",
                         "latencyMs": _ms(t0),
                         "detail": "内部 %s · HTTP %s" % (config.KK_BASE, resp.status_code)})
    except Exception as exc:
        services.append({"key": "kkfileview", "name": "自研预览引擎（内核：kkFileView）",
                         "status": "down", "latencyMs": _ms(t0),
                         "detail": "不可达: %s" % str(exc)[:100]})

    # 3. MySQL
    t0 = time.perf_counter()
    try:
        await db.query_one("SELECT 1 AS ok")
        today = await db.query_one(
            "SELECT COUNT(*) c FROM access_log WHERE created_at >= CURDATE()")
        services.append({"key": "mysql", "name": "统计库 MySQL 8", "status": "up",
                         "latencyMs": _ms(t0),
                         "detail": "今日明细 %s 条 · 宿主端口 13306" % today["c"]})
    except Exception as exc:
        services.append({"key": "mysql", "name": "统计库 MySQL 8", "status": "down",
                         "latencyMs": _ms(t0), "detail": str(exc)[:100]})

    # 4. Redis
    t0 = time.perf_counter()
    try:
        r = await db.get_redis()
        await r.ping()
        keys = await r.dbsize()
        services.append({"key": "redis", "name": "缓存 Redis 7", "status": "up",
                         "latencyMs": _ms(t0),
                         "detail": "%s 个键 · 宿主端口 16379" % keys})
    except Exception as exc:
        services.append({"key": "redis", "name": "缓存 Redis 7", "status": "down",
                         "latencyMs": _ms(t0), "detail": str(exc)[:100]})

    # 运行指标
    queue = await db.query_one(
        "SELECT COUNT(*) c FROM preview_task WHERE status IN ('queued','converting')")
    failed = await db.query_one(
        "SELECT COUNT(*) c FROM preview_task WHERE status='failed'")
    # 「打不开的文件」要按文件去重、且排除后来已经转成功的。
    # 直接数 status='failed' 的记录数会偏大：同一个文件失败两次算两条，
    # 而且失败记录会永久留着 —— 实测面板显示 82，真实打不开的只有 62 个文件。
    failed_files = await db.query_one(
        "SELECT COUNT(*) c FROM (SELECT file_key FROM preview_task "
        "GROUP BY file_key HAVING SUM(status='failed') > 0 AND SUM(status='ready') = 0) t")
    failed_recent = await db.query_one(
        "SELECT COUNT(*) c FROM preview_task WHERE status='failed' "
        "AND created_at >= NOW() - INTERVAL 1 DAY")
    today_err = await db.query_one(
        "SELECT COUNT(*) c FROM access_log WHERE created_at >= CURDATE() AND success=0")
    today_all = await db.query_one(
        "SELECT COUNT(*) c FROM access_log WHERE created_at >= CURDATE()")
    ready = await db.query_one(
        "SELECT COUNT(*) c FROM preview_task WHERE status='ready'")
    ready_files = await db.query_one(
        "SELECT COUNT(DISTINCT file_key) c FROM preview_task WHERE status='ready'")

    metrics = {
        "convertingQueue": int(queue["c"]) if queue else 0,
        # 累计失败记录数（只增不减，看趋势用；判断"现在几个文件打不开"用 failedFiles）
        "failedTasks": int(failed["c"]) if failed else 0,
        # 至今没有任何成功产物的文件数 —— 这才是"现在打不开的有几个"
        "failedFiles": int(failed_files["c"]) if failed_files else 0,
        # 近 24 小时新增的失败：如果这个数在涨，说明是刚出的问题
        "failedRecent": int(failed_recent["c"]) if failed_recent else 0,
        "readyTasks": int(ready["c"]) if ready else 0,
        "readyFiles": int(ready_files["c"]) if ready_files else 0,
        "todayRequests": int(today_all["c"]) if today_all else 0,
        "todayErrors": int(today_err["c"]) if today_err else 0,
        "maxConcurrentConvert": config.MAX_CONCURRENT_CONVERT,
        "checkedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return _wrap({"services": services, "metrics": metrics,
                  "upCount": sum(1 for s in services if s["status"] == "up"),
                  "total": len(services)})

# 全局时间范围：看板顶部一个选择器统一控制所有页面。
# hour=最近24小时 / day=最近30天 / week=最近12周 / month=最近12个月 / total=最近10年
RANGE_PATTERN = "^(hour|day|week|month|total)$"
TREND_GRAN = {"hour": "hour", "day": "day", "week": "week",
              "month": "month", "total": "month"}
RANGE_LABEL = {"hour": "最近 24 小时", "day": "最近 30 天", "week": "最近 12 周",
               "month": "最近 12 个月", "total": "全部（近 10 年）"}


def _range_bounds(range_key: str):
    """返回 (start, end_exclusive)。所有 SQL 统一写 created_at >= start AND < end。"""
    now = datetime.datetime.now()
    if range_key == "hour":
        return ((now - datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
                (now + datetime.timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"))
    today = now.date()
    if range_key == "week":
        start = today - datetime.timedelta(weeks=12)
    elif range_key == "month":
        start = today - datetime.timedelta(days=365)
    elif range_key == "total":
        start = today - datetime.timedelta(days=3652)      # 约 10 年
    else:                                                  # day
        start = today - datetime.timedelta(days=30)
    return start.isoformat(), (today + datetime.timedelta(days=1)).isoformat()


def _sys_sql(alias: str = "l"):
    """排除后台任务账号（预转换 / 预热）。

    这两类请求也走 /preview/url、也写 access_log，但它们不是"用户在打开文件"：
    预转换扫的是整个受控目录，命中大量早已删除的文件，一天能刷出上千条 404。
    实测 30 天 5882 条失败里 88.3% 来自它们，混进用户指标会把成功率
    从真实的 96.2% 压到 37% —— 看板在报一个不存在的问题。
    要排查后台任务本身，用 ?sys=1 打开（见 stats_failures）。
    """
    if not config.SYSTEM_EMPS:
        return "", []
    ph = ",".join(["%s"] * len(config.SYSTEM_EMPS))
    return (" AND " + alias + ".emp_no NOT IN (" + ph + ")"), list(config.SYSTEM_EMPS)


def _scope_sql(scope: str, alias: str = "l", include_sys: bool = False):
    """'_all' 表示全站（管理员），否则按系统过滤；默认剔除后台任务账号。"""
    parts, args = [], []
    if scope != "_all":
        parts.append(alias + ".app_id=%s")
        args.append(scope)
    if not include_sys:
        s, sargs = _sys_sql(alias)
        if s:
            parts.append(s.replace(" AND ", "", 1))
            args += sargs
    if not parts:
        return "", []
    return " AND " + " AND ".join(parts), args


def _date_range(range_key: str, from_date: str | None = None,
                to_date: str | None = None):
    """兼容旧写法：返回 [start, end] 两个日期（含首尾）。"""
    if from_date and to_date:
        return from_date, to_date
    start, end = _range_bounds(range_key)
    return start[:10], end[:10]


@router.get("/stats/summary")
async def stats_summary(range: str = Query("day", pattern=RANGE_PATTERN),
                        appId: str = "", request: Request = None,
                        account: dict = Depends(get_viewer)):
    """当前时间范围的汇总。看板所有页面共用这一个口径。"""
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, appId)
    start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    row = await db.query_one(
        "SELECT COUNT(*) total, SUM(l.success) success, SUM(1-l.success) fail,"
        " SUM(l.cache_hit) cache_hit,"
        # 延迟只统计 action='preview'（一次预览的一条记录，cost_ms 已被回填成
        # 端到端加载耗时）。open / 文件流那几条是 0~几毫秒，混进来会把平均值
        # 稀释成 0.4 秒，与用户体感的 20 秒完全对不上。
        " ROUND(AVG(CASE WHEN l.e2e=1 THEN l.cost_ms END)) avg_cost,"
        " MAX(CASE WHEN l.e2e=1 THEN l.cost_ms END) max_cost,"
        " MIN(CASE WHEN l.e2e=1 THEN l.cost_ms END) min_cost,"
        " SUM(l.e2e) e2e_cnt,"
        " COUNT(DISTINCT l.emp_no) uniq_emp, COUNT(DISTINCT l.file_key) uniq_file"
        " FROM access_log l WHERE l.created_at >= %s AND l.created_at < %s" + f,
        [start, end] + args) or {}
    out = {k: (v if v is not None else 0) for k, v in row.items()}
    # 加载超时次数：探针等满上限(60/120 秒)仍没看到内容。这类样本**不计入**
    # 加载耗时（那是探针参数不是真实数据，见 loadmetric._write_back_timeout），
    # 但要让人看得见 —— 它代表"用户盯着白屏等了两分钟"。
    # Redis 只留最近 40 天，range 更长时按 0 处理（历史值看失败明细）。
    out["timeout_cnt"] = 0
    if range in ("hour", "day"):
        try:
            out["timeout_cnt"] = await loadmetric.timeout_count()
        except Exception:
            pass
    out.update({"range": range, "rangeLabel": RANGE_LABEL.get(range, range),
                "appId": scope, "start": start, "end": end})
    return _wrap(out)


@router.get("/stats/trend")
async def stats_trend(granularity: str = "", appId: str = "", request: Request = None,
                      account: dict = Depends(get_viewer)):
    """访问量与平均延迟趋势。粒度默认跟随全局时间范围。"""
    if request:
        request.state.app_id = account["app_id"]
    range_key = "day"
    if request is not None:
        range_key = request.query_params.get("range", "day")
    if range_key not in TREND_GRAN:
        range_key = "day"
    gran = granularity if granularity in ("hour", "day", "week", "month") \
        else TREND_GRAN[range_key]
    scope = viewer_scope(account, appId)
    start, end = _range_bounds(range_key)
    f, args = _scope_sql(scope)
    label = {
        "hour": "DATE_FORMAT(l.created_at, '%%Y-%%m-%%d %%H:00')",
        "day": "DATE_FORMAT(l.created_at, '%%Y-%%m-%%d')",
        "week": "DATE_FORMAT(l.created_at, '%%x-W%%v')",
        "month": "DATE_FORMAT(l.created_at, '%%Y-%%m')",
    }[gran]
    rows = await db.query(
        "SELECT " + label + " label, COUNT(*) total,"
        " ROUND(AVG(CASE WHEN l.e2e=1 THEN l.cost_ms END)) avg_cost,"
        " MAX(CASE WHEN l.e2e=1 THEN l.cost_ms END) max_cost,"
        " MIN(CASE WHEN l.e2e=1 THEN l.cost_ms END) min_cost,"
        " SUM(l.success) success, SUM(1-l.success) fail"
        " FROM access_log l WHERE l.created_at >= %s AND l.created_at < %s" + f +
        " GROUP BY label ORDER BY label", [start, end] + args)
    return _wrap([_num(r) for r in rows])


@router.get("/stats/apps")
async def stats_apps(range: str = "day", top: int = 20, request: Request = None,
                     account: dict = Depends(get_viewer)):
    """哪些系统在访问。普通接入系统只能看到自己一行。"""
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, "")
    start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    rows = await db.query(
        "SELECT l.app_id, IFNULL(NULLIF(a.app_name,''), l.app_id) app_name,"
        " COUNT(*) total, SUM(l.success) success, SUM(1-l.success) fail,"
        " ROUND(AVG(CASE WHEN l.e2e=1 THEN l.cost_ms END)) avg_cost,"
        " MAX(CASE WHEN l.e2e=1 THEN l.cost_ms END) max_cost,"
        " MIN(CASE WHEN l.e2e=1 THEN l.cost_ms END) min_cost,"
        " COUNT(DISTINCT l.emp_no) uniq_emp,"
        " COUNT(DISTINCT l.file_key) uniq_file"
        " FROM access_log l LEFT JOIN app_account a ON a.app_id = l.app_id"
        " WHERE l.created_at >= %s AND l.created_at < %s AND l.app_id <> ''" + f +
        " GROUP BY l.app_id ORDER BY total DESC LIMIT %s", [start, end] + args + [top])
    return _wrap([_num(r) for r in rows])


@router.get("/stats/files")
async def stats_files(range: str = "day", top: int = 20, orderBy: str = "view_cnt",
                      request: Request = None, account: dict = Depends(get_viewer)):
    """通过接口浏览了哪些文件，含来源系统。"""
    if request:
        request.state.app_id = account["app_id"]
    order = "view_cnt" if orderBy not in ("view_cnt", "avg_cost", "max_cost",
                                          "last_view_at") else orderBy
    scope = viewer_scope(account, "")
    start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    rows = await db.query(
        "SELECT l.file_key, MAX(l.file_name) file_name, MAX(l.file_ext) file_ext,"
        " MAX(l.file_type) file_type, COUNT(*) view_cnt,"
        " COUNT(DISTINCT l.emp_no) uniq_emp, COUNT(DISTINCT l.app_id) app_cnt,"
        " GROUP_CONCAT(DISTINCT IFNULL(NULLIF(a.app_name,''), l.app_id)"
        "   ORDER BY l.app_id SEPARATOR '、') apps,"
        " ROUND(AVG(CASE WHEN l.e2e=1 THEN l.cost_ms END)) avg_cost,"
        " MAX(CASE WHEN l.e2e=1 THEN l.cost_ms END) max_cost,"
        " MIN(CASE WHEN l.e2e=1 THEN l.cost_ms END) min_cost,"
        " MAX(l.created_at) last_view_at"
        " FROM access_log l LEFT JOIN app_account a ON a.app_id = l.app_id"
        " WHERE l.created_at >= %s AND l.created_at < %s" + f +
        " GROUP BY l.file_key ORDER BY " + order + " DESC LIMIT %s",
        [start, end] + args + [top])
    return _wrap([_num(r) for r in rows])


@router.get("/stats/latency")
async def stats_latency(range: str = "day", appId: str = "", request: Request = None,
                        account: dict = Depends(get_viewer)):
    """延迟分布: avg/min/max 与直方图(估算 P50/P95/P99)。

    只统计 action='preview' 这条记录（一次预览一条，cost_ms 已回填为端到端加载耗时）。
    open / 文件流是 0~几毫秒的跳转与静态资源，算进来只会把分布压到最左边。
    直方图直接从明细表现算，跟着全局时间范围走（聚合表只到"天"，粒度不够）。
    """
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, appId)
    start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    summary = await db.query_one(
        "SELECT COUNT(*) cnt, ROUND(AVG(l.cost_ms)) avg_cost, MAX(l.cost_ms) max_cost,"
        " MIN(l.cost_ms) min_cost FROM access_log l"
        " WHERE l.created_at >= %s AND l.created_at < %s AND l.e2e=1" + f,
        [start, end] + args) or {}
    hist = await db.query(
        "SELECT FLOOR(l.cost_ms / 100) * 100 bucket_lo, COUNT(*) cnt"
        " FROM access_log l WHERE l.created_at >= %s AND l.created_at < %s"
        " AND l.e2e=1" + f +
        " GROUP BY bucket_lo ORDER BY bucket_lo", [start, end] + args)
    total = sum(int(h["cnt"]) for h in hist) or 1
    buckets, pct = [], {}
    for h in hist:
        buckets.append({"lo": int(h["bucket_lo"]), "cnt": int(h["cnt"])})
    for p in (50, 95, 99):
        target = total * p / 100
        acc = 0
        for b in buckets:
            if acc + b["cnt"] >= target:
                pct["p%d" % p] = b["lo"]
                break
            acc += b["cnt"]
        pct.setdefault("p%d" % p, buckets[-1]["lo"] if buckets else 0)
    return _wrap({"summary": _num(summary), "histogram": buckets,
                  "percentile": pct, "total": total})


@router.get("/stats/errors")
async def stats_errors(range: str = "day", days: int = 0, top: int = 10,
                       request: Request = None, account: dict = Depends(get_viewer)):
    """失败原因汇总。days>0 时按"最近 N 天"（失败原因默认看 180 天）。"""
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, "")
    if days and days > 0:
        days = min(days, 180)
        end_dt = datetime.date.today() + datetime.timedelta(days=1)
        start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        end = end_dt.isoformat()
    else:
        start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    rows = await db.query(
        "SELECT l.status_code, l.error_msg, COUNT(*) cnt,"
        " COUNT(DISTINCT l.emp_no) uniq_emp, COUNT(DISTINCT l.app_id) app_cnt,"
        " GROUP_CONCAT(DISTINCT IFNULL(NULLIF(a.app_name,''), l.app_id)"
        "   ORDER BY l.app_id SEPARATOR '、') apps,"
        " MAX(l.created_at) last_at"
        " FROM access_log l LEFT JOIN app_account a ON a.app_id = l.app_id"
        " WHERE l.success=0 AND l.created_at >= %s AND l.created_at < %s" + f +
        " GROUP BY l.status_code, l.error_msg ORDER BY cnt DESC LIMIT %s",
        [start, end] + args + [top])
    return _wrap([_num(r) for r in rows])


# ------------------------------------------------------------------ 告警记录
# 口径：access_log 里 success=0 的每一次失败（一次失败 = 一条告警）。
# 为什么用访问明细而不是审计表：审计表按「工号+文件+动作+秒」唯一约束，同一秒的
# 多次失败会被并成一条，条数对不上；访问明细每次请求都有一行，能如实反映次数。
_ALERT_KIND_SQL = """CASE
    WHEN l.status_code = 429 THEN '限流'
    WHEN INSTR(l.error_msg, '加密') > 0 THEN '加密文件'
    WHEN INSTR(l.error_msg, '不可访问') > 0 OR l.status_code IN (403, 404)
         THEN '源文件不可达'
    WHEN INSTR(l.error_msg, '超时') > 0 OR INSTR(LOWER(l.error_msg), 'timeout') > 0
         THEN '超时'
    WHEN l.status_code >= 500 THEN '服务异常'
    WHEN INSTR(l.error_msg, '转码失败') > 0 OR INSTR(l.error_msg, '转换失败') > 0
         THEN '转换失败'
    ELSE '其它' END"""


def _alert_where(days, appId, empNo, keyword, status, scope, include_sys=False):
    """返回 (where_sql, args)。days 上限 180 —— 与 RETENTION_DAYS 保持一致。

    include_sys=True 时把预转换/预热账号也算进来：这两个账号会大面积刷 404
    （扫到已删除的文件），默认排除，免得失败列表被它们占满。
    """
    days = max(1, min(int(days or 180), 180))
    start = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    where = ["l.success=0", "l.created_at >= %s"]
    args = [start]
    # 显式按工号筛时不再排除系统账号：用户既然点名 PRECONV，就是想看它的记录，
    # 再套一层 NOT IN 会得到空结果，看着像"后台任务从没失败过"。
    f, fargs = _scope_sql(scope, include_sys=include_sys or bool(empNo))
    if f:
        where.append(f.replace(" AND ", "", 1))
        args += fargs
    if empNo:
        where.append("l.emp_no LIKE %s")
        args.append("%" + empNo + "%")
    if appId:
        where.append("l.app_id=%s")
        args.append(appId)
    if keyword:
        where.append("(l.file_name LIKE %s OR l.error_msg LIKE %s)")
        args += ["%" + keyword + "%", "%" + keyword + "%"]
    codes = [c.strip() for c in (status or "").split(",") if c.strip().isdigit()]
    if codes:
        where.append("l.status_code IN (" + ",".join(["%s"] * len(codes)) + ")")
        args += [int(c) for c in codes]
    return " WHERE " + " AND ".join(where), args, days


@router.get("/stats/alerts")
async def stats_alerts(days: int = 30, kind: str = "", level: str = "",
                       status: str = "", keyword: str = "", export: str = "",
                       page: int = 1, size: int = 50, request: Request = None,
                       account: dict = Depends(get_viewer)):
    """服务告警（容量/压力维度），默认最近 30 天。

    与 /stats/failures 的区别：那边是"某个文件为什么打不开"（一次一条），
    这里是"服务是不是扛不住了"——多系统同时被限流、排队超 5 分钟、CPU/内存/磁盘
    逼近限额、转换异常。一条告警从触发到恢复只有一个生命周期，
    所以能算持续时长与峰值（见 alerts.py）。

    筛选：kind（规则）、level（warn/critical）、status（open/resolved）、keyword。
    """
    if request:
        request.state.app_id = account["app_id"]
    days = max(1, min(int(days or 30), 180))

    where = ["first_at >= NOW() - INTERVAL %s DAY"]
    args = [days]
    if kind:
        where.append("kind=%s")
        args.append(kind)
    if level:
        where.append("level=%s")
        args.append(level)
    if status:
        where.append("status=%s")
        args.append(status)
    if keyword:
        where.append("(title LIKE %s OR detail LIKE %s OR target LIKE %s)")
        args += ["%" + keyword + "%"] * 3
    wsql = " WHERE " + " AND ".join(where)

    if export == "csv":
        rows = await db.query(
            "SELECT * FROM alert_log" + wsql +
            " ORDER BY status='open' DESC, first_at DESC LIMIT 50000", args)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["触发时间", "恢复时间", "类型", "级别", "对象",
                         "当前值", "峰值", "阈值", "单位", "持续(分钟)", "状态", "详情"])
        for r in rows:
            dur = _dur_min(r)
            writer.writerow([r["first_at"], r["resolved_at"] or "", r["title"],
                             r["level"], r["target"], _fmt(r["value"]),
                             _fmt(r["peak"]), _fmt(r["threshold"]), r["unit"],
                             dur, "未恢复" if r["status"] == "open" else "已恢复",
                             r["detail"]])
        buf.seek(0)
        name = "alerts-%s.csv" % datetime.date.today().isoformat()
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=%s" % name})

    size = max(1, min(int(size or 50), 200))
    page = max(1, int(page or 1))

    # 卡片用总数：当前未恢复不受 days 影响（三个月前触发、现在还没好的必须看见）
    open_row = await db.query_one(
        "SELECT COUNT(*) c, SUM(level='critical') crit FROM alert_log"
        " WHERE status='open'")
    agg = await db.query_one(
        "SELECT COUNT(*) total, SUM(first_at >= CURDATE()) today,"
        " SUM(level='critical') crit,"
        " MAX(TIMESTAMPDIFF(SECOND, first_at, IFNULL(resolved_at, NOW()))) longest"
        " FROM alert_log" + wsql, args)
    total = await db.query_one("SELECT COUNT(*) c FROM alert_log" + wsql, args)
    by_day = await db.query(
        "SELECT DATE(first_at) d, COUNT(*) n, SUM(level='critical') crit"
        " FROM alert_log" + wsql + " GROUP BY d ORDER BY d", args)
    by_kind = await db.query(
        "SELECT kind, MAX(title) title, MAX(level) level, COUNT(*) n,"
        " SUM(status='open') open_n FROM alert_log" + wsql +
        " GROUP BY kind ORDER BY n DESC", args)
    rows = await db.query(
        "SELECT * FROM alert_log" + wsql +
        " ORDER BY status='open' DESC, first_at DESC LIMIT %s OFFSET %s",
        args + [size, (page - 1) * size])

    out = []
    for r in rows:
        d = _num(r)
        d["durationMin"] = _dur_min(r)
        d["value"] = _fmt(r["value"])
        d["peak"] = _fmt(r["peak"])
        d["threshold"] = _fmt(r["threshold"])
        out.append(d)

    return _wrap({
        "days": days, "page": page, "size": size,
        "total": int((total or {}).get("c") or 0),
        "summary": {
            "open": int((open_row or {}).get("c") or 0),
            "openCritical": int((open_row or {}).get("crit") or 0),
            "total": int((agg or {}).get("total") or 0),
            "today": int((agg or {}).get("today") or 0),
            "critical": int((agg or {}).get("crit") or 0),
            "longestMin": int(((agg or {}).get("longest") or 0) // 60),
        },
        "byDay": [_num(r) for r in by_day],
        "byKind": [_num(r) for r in by_kind],
        "list": out,
    })


def _fmt(v):
    """数值展示：整数不显示小数点，小数保留两位（0.0 也会被写成 "0"）。"""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return v
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return "%.2f" % f


def _dur_min(row):
    t0 = row.get("first_at")
    t1 = row.get("resolved_at") or datetime.datetime.now()
    if not t0:
        return 0
    try:
        return max(0, int((t1 - t0).total_seconds() // 60))
    except TypeError:
        return 0


@router.get("/stats/failures")
async def stats_failures(days: int = 180, appId: str = "", empNo: str = "",
                         keyword: str = "", status: str = "", kind: str = "",
                         sys: int = 0,
                         export: str = "", page: int = 1, size: int = 50,
                         request: Request = None,
                         account: dict = Depends(get_viewer)):
    """预览失败原因列表 + 汇总（默认最近 180 天，最多 180 天）。

    **这是"为什么没打开"，不是服务告警**：源文件已删除（404）、无权限（403）、
    文档加密（422）、单次被限流（429）、服务异常（5xx）。一次失败一条，
    用于回答"哪些文件打不开、为什么"。容量/压力维度的告警见 /stats/alerts。

    sys=1 时把预转换/预热账号也算进来。默认不算 —— 它们扫整个受控目录，
    会命中大量已删除文件刷出上千条 404，把真实用户失败淹掉。
    """
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, "")
    wsql, args, days = _alert_where(days, appId, empNo, keyword, status, scope,
                                    include_sys=bool(sys))

    inner = ("SELECT l.id, l.created_at, l.app_id, l.emp_no, l.emp_name, l.dept,"
             " l.file_key, l.file_name, l.file_ext, l.status_code, l.client_ip,"
             " l.error_msg, " + _ALERT_KIND_SQL + " AS kind"
             " FROM access_log l" + wsql)
    kind_sql, kind_args = "", []
    if kind:
        kind_sql = " WHERE t.kind=%s"
        kind_args = [kind]

    if export == "csv":
        rows = await db.query(
            "SELECT t.* FROM (" + inner + ") t" + kind_sql +
            " ORDER BY t.id DESC LIMIT 50000", args + kind_args)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["时间", "系统", "工号", "姓名", "部门", "文件名",
                         "扩展名", "告警类型", "状态码", "原因", "IP"])
        for r in rows:
            writer.writerow([r["created_at"], r["app_id"], r["emp_no"],
                             r["emp_name"], r["dept"], r["file_name"],
                             r["file_ext"], r["kind"], r["status_code"],
                             r["error_msg"], r["client_ip"]])
        buf.seek(0)
        name = "alerts-%s.csv" % datetime.date.today().isoformat()
        return StreamingResponse(
            iter([buf.getvalue().encode("utf-8-sig")]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=%s" % name})

    size = max(1, min(int(size or 50), 200))
    page = max(1, int(page or 1))

    total = await db.query_one(
        "SELECT COUNT(*) c FROM (" + inner + ") t" + kind_sql,
        args + kind_args)
    today_start = datetime.date.today().isoformat()
    agg = await db.query_one(
        "SELECT COUNT(*) total, COUNT(DISTINCT t.file_key) files,"
        " COUNT(DISTINCT t.app_id) apps, COUNT(DISTINCT t.emp_no) emps,"
        " SUM(t.created_at >= %s) today"
        " FROM (" + inner + ") t" + kind_sql,
        [today_start] + args + kind_args)
    by_day = await db.query(
        "SELECT DATE(t.created_at) d, COUNT(*) n FROM (" + inner + ") t" + kind_sql +
        " GROUP BY d ORDER BY d", args + kind_args)
    by_kind = await db.query(
        "SELECT t.kind, COUNT(*) n FROM (" + inner + ") t" + kind_sql +
        " GROUP BY t.kind ORDER BY n DESC", args + kind_args)
    by_status = await db.query(
        "SELECT t.status_code, COUNT(*) n FROM (" + inner + ") t" + kind_sql +
        " GROUP BY t.status_code ORDER BY n DESC", args + kind_args)
    by_app = await db.query(
        "SELECT t.app_id, MAX(IFNULL(a.app_name, '')) app_name, COUNT(*) n"
        " FROM (" + inner + ") t LEFT JOIN app_account a ON a.app_id=t.app_id" +
        kind_sql + " GROUP BY t.app_id ORDER BY n DESC LIMIT 12",
        args + kind_args)
    rows = await db.query(
        "SELECT t.* FROM (" + inner + ") t" + kind_sql +
        " ORDER BY t.id DESC LIMIT %s OFFSET %s",
        args + kind_args + [size, (page - 1) * size])

    return _wrap({
        "days": days, "total": int((total or {}).get("c") or 0),
        "page": page, "size": size,
        "summary": {
            "total": int((agg or {}).get("total") or 0),
            "files": int((agg or {}).get("files") or 0),
            "apps": int((agg or {}).get("apps") or 0),
            "emps": int((agg or {}).get("emps") or 0),
            "today": int((agg or {}).get("today") or 0),
        },
        "byDay": [_num(r) for r in by_day],
        "byKind": [_num(r) for r in by_kind],
        "byStatus": [_num(r) for r in by_status],
        "byApp": [_num(r) for r in by_app],
        "list": [_num(r) for r in rows],
    })


@router.get("/stats/employees")
async def stats_employees(range: str = "day", top: int = 20, request: Request = None,
                          account: dict = Depends(get_viewer)):
    """工号活跃 TOP。按「工号 + 系统」分组，能看出谁在哪个系统用得多。"""
    if request:
        request.state.app_id = account["app_id"]
    scope = viewer_scope(account, "")
    start, end = _range_bounds(range)
    f, args = _scope_sql(scope)
    rows = await db.query(
        "SELECT l.emp_no, MAX(l.emp_name) emp_name, MAX(l.dept) dept,"
        " l.app_id, IFNULL(NULLIF(a.app_name,''), l.app_id) app_name,"
        " COUNT(*) cnt, COUNT(DISTINCT l.file_key) files,"
        " ROUND(AVG(CASE WHEN l.e2e=1 THEN l.cost_ms END)) avg_cost,"
        " MAX(l.created_at) last_at"
        " FROM access_log l LEFT JOIN app_account a ON a.app_id = l.app_id"
        " WHERE l.created_at >= %s AND l.created_at < %s" + f +
        " GROUP BY l.emp_no, l.app_id ORDER BY cnt DESC LIMIT %s",
        [start, end] + args + [top])
    return _wrap([_num(r) for r in rows])


@router.get("/stats/realtime")
async def stats_realtime(request: Request = None, account: dict = Depends(get_viewer)):
    """今日实时计数(来自 Redis, 秒级)。普通系统只取自己那一路计数。"""
    if request:
        request.state.app_id = account["app_id"]
    try:
        r = await db.get_redis()
        day = datetime.date.today().isoformat()
        if can_all(account):
            keys = await r.keys("rt:day:%s:*" % day)
        else:
            mine = "rt:day:%s:%s" % (day, account["app_id"])
            keys = [mine] if await r.exists(mine) else []
        total = 0
        apps = {}
        if keys:
            vals = await r.mget(keys)
            for k, v in zip(keys, vals):
                app = k.split(":")[-1]
                n = int(v or 0)
                # 普通系统只看自己: 直接用自己的计数, 不把全局 _all 也算进去
                if app == "_all":
                    if not can_all(account):
                        continue
                total += n
                if app != "_all":
                    apps[app] = n
        return _wrap({"today": total, "apps": apps})
    except Exception as exc:
        return _wrap({"today": 0, "apps": {}, "warn": str(exc)})


@router.get("/audit/logs")
async def audit_logs(empNo: str = "", appId: str = "", fileKey: str = "",
                     from_: str = Query("", alias="from"),
                     to: str = "", keyword: str = "", export: str = "",
                     result: str = "", orderBy: str = "", range: str = "",
                     page: int = 1, size: int = 20, request: Request = None,
                     account: dict = Depends(get_viewer)):
    """审计日志检索, 支持 CSV 导出。普通接入系统只能看自己的记录。

    result=success   只看成功（读审计表）
    result=fail      只看失败：**改读 access_log**。审计表按
                     「工号+文件+动作+秒」做了唯一约束（防重复点击写多条），
                     同一秒的两次失败会被并成一条；而访问明细每一次都有，
                     看板上的"失败 N 次"也是从它算的，只有这样点进来条数才对得上。
    range=hour|day|week|month|total
                     时间窗跟随看板顶部的全局选择器（不传则不限时间）
    orderBy=cost_desc|cost_asc
                     按耗时排序，用于定位"最慢 / 最快"的那一次。
    """
    if request:
        request.state.app_id = account["app_id"]
    admin = can_all(account)
    fail_mode = result in ("fail", "error", "failed")
    # 按耗时排序（点"最大延迟/最快速度"进来）同样要读访问明细：
    # 审计表只有"申请预览"这一种动作，真正最慢的那次可能是页面渲染或文件流，
    # 只查审计表会指到一条并不是最慢的记录上。
    use_access = (fail_mode or result == "success"
                  or orderBy in ("cost_desc", "cost_asc"))
    table = "access_log" if use_access else "audit_log"

    where, args = [], []
    if not admin:
        # 强制收口: 忽略传入的 appId, 只返回自己系统产生的记录
        where.append("app_id=%s"); args.append(account["app_id"])
    elif appId:
        where.append("app_id=%s"); args.append(appId)
    if empNo:
        where.append("emp_no LIKE %s"); args.append("%" + empNo + "%")
    if fileKey:
        where.append("file_key=%s"); args.append(fileKey)
    if fail_mode:
        where.append("success=0")
    elif result == "success":
        where.append("success=1" if use_access else "result='success'")
    if range in RANGE_LABEL:
        r_start, r_end = _range_bounds(range)
        where.append("created_at >= %s"); args.append(r_start)
        where.append("created_at < %s"); args.append(r_end)
    if from_:
        where.append("created_at >= %s"); args.append(from_)
    if to:
        where.append("created_at <= %s"); args.append(to + " 23:59:59")
    if keyword:
        where.append("(file_name LIKE %s OR file_url LIKE %s)" if not use_access
                     else "(file_name LIKE %s OR error_msg LIKE %s)")
        args += ["%" + keyword + "%", "%" + keyword + "%"]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = {"cost_desc": "cost_ms DESC, id DESC",
                 "cost_asc": "cost_ms ASC, id ASC"}.get(orderBy, "id DESC")

    if use_access:
        cols = ("id, created_at, emp_no, emp_name, dept, app_id, file_key, file_name,"
                " '' AS file_url, action, IF(success=1,'success','error') AS result,"
                " error_msg AS detail, client_ip, cost_ms, status_code")
        csv_cols = ("created_at, emp_no, emp_name, dept, app_id, file_name, action,"
                    " IF(success=1,'success','error') AS result, status_code,"
                    " error_msg AS detail, client_ip, cost_ms")
    else:
        cols = ("id, created_at, emp_no, emp_name, dept, app_id, file_key, file_name,"
                " file_url, action, result, detail, client_ip, cost_ms,"
                " 0 AS status_code")
        csv_cols = ("created_at, emp_no, emp_name, dept, app_id, file_name, file_url,"
                    " action, result, detail, client_ip, cost_ms, 0 AS status_code")

    if export == "csv":
        rows = await db.query(
            "SELECT " + csv_cols + " FROM " + table + wsql +
            " ORDER BY " + order_sql + " LIMIT 50000", args or None)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["时间", "工号", "姓名", "部门", "系统", "文件名",
                         "文件地址/动作", "结果", "状态码", "详情", "IP", "耗时(ms)"])
        for r in rows:
            writer.writerow([r["created_at"], r["emp_no"], r["emp_name"], r["dept"],
                             r["app_id"], r["file_name"],
                             r.get("file_url") or r.get("action"),
                             r["result"], r.get("status_code", 0), r["detail"],
                             r["client_ip"], r["cost_ms"]])
        buf.seek(0)
        name = "audit-%s.csv" % datetime.date.today().isoformat()
        return StreamingResponse(iter([buf.getvalue().encode("utf-8-sig")]),
                                 media_type="text/csv",
                                 headers={"Content-Disposition": "attachment; filename=%s" % name})

    total = await db.query_one("SELECT COUNT(*) c FROM " + table + wsql, args or None)
    rows = await db.query(
        "SELECT " + cols + " FROM " + table + wsql +
        " ORDER BY " + order_sql + " LIMIT %s OFFSET %s",
        (args or []) + [size, (page - 1) * size])
    return _wrap({"total": int(total["c"]) if total else 0, "page": page,
                  "size": size, "orderBy": orderBy or "time",
                  "source": "access" if use_access else "audit",
                  "range": range if range in RANGE_LABEL else "",
                  "list": [_num(r) for r in rows]})


@router.get("/audit/detail")
async def audit_detail(id: int = 0, source: str = "audit",
                       request: Request = None,
                       account: dict = Depends(get_viewer)):
    """单条记录的全部信息（失败弹窗用）。

    source=audit 时按 request_id 关联访问明细，补出状态码、失败原因、
    上游耗时、User-Agent —— 审计表本身只存业务字段，这些在 access_log 里。
    """
    if request:
        request.state.app_id = account["app_id"]
    if source == "access":
        row = await db.query_one(
            "SELECT id, request_id, created_at, emp_no, emp_name, dept, app_id,"
            " file_key, file_name, file_ext, file_type, action, cost_ms, upstream_ms,"
            " cache_hit, is_async, status_code, success, error_msg, client_ip,"
            " user_agent FROM access_log WHERE id=%s", (id,))
    else:
        row = await db.query_one(
            "SELECT id, request_id, created_at, emp_no, emp_name, dept, app_id,"
            " file_key, file_name, file_ext, file_url, action, result, detail,"
            " client_ip, cost_ms FROM audit_log WHERE id=%s", (id,))
        if row:
            # access_log 没有 file_url（地址只在审计表里），别去取它
            extra = await db.query_one(
                "SELECT status_code, error_msg, upstream_ms, cache_hit, user_agent"
                " FROM access_log WHERE request_id=%s ORDER BY id DESC LIMIT 1",
                (row["request_id"],))
            if extra:
                row["detail"] = row.get("detail") or extra.get("error_msg") or ""
                for k in ("status_code", "error_msg", "upstream_ms", "cache_hit",
                          "user_agent"):
                    row[k] = extra.get(k)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在或已超出保留期")
    if not can_all(account) and row.get("app_id") != account["app_id"]:
        raise HTTPException(status_code=403, detail="无权查看该记录")
    out = _num(row)
    out["ok"] = bool(row.get("success", row.get("result") == "success"))
    out["statusCode"] = int(row.get("status_code") or 0)
    out["errorMsg"] = row.get("error_msg") or row.get("detail") or ""
    out["userAgent"] = row.get("user_agent") or ""
    out["upstreamMs"] = int(row.get("upstream_ms") or 0)
    out["cacheHit"] = bool(row.get("cache_hit") or 0)
    out["fileUrl"] = row.get("file_url") or ""
    out["requestId"] = row.get("request_id") or ""
    return _wrap(out)


@router.post("/admin/aggregate")
async def run_aggregate(_=Depends(require_admin)):
    from . import collector
    await collector.aggregate_once(days_back=3)
    return _wrap({"msg": "聚合完成"})


def _num(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime.datetime):
            out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(v, datetime.date):
            out[k] = v.isoformat()
        elif hasattr(v, "quantize"):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def _wrap(data):
    return {"code": 0, "msg": "ok", "data": data}
