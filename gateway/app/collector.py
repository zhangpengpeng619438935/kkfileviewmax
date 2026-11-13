import asyncio
import logging
import time

from . import config, db

log = logging.getLogger("collector")

_queue: asyncio.Queue | None = None
_running = False

BUCKETS = [(0, 50), (50, 100), (100, 200), (200, 500), (500, 1000),
           (1000, 3000), (3000, 10000), (10000, 99999999)]


def q() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=config.COLLECT_QUEUE_SIZE)
    return _queue


def bucket_of(cost_ms: int) -> int:
    for lo, hi in BUCKETS:
        if lo <= cost_ms < hi:
            return lo
    return BUCKETS[-1][0]


_WHO = {}          # emp_no -> (emp_name, dept)，用于补全后续请求里缺失的姓名部门
_AUDIT_DEDUP = {}  # (emp_no, file_key, action) -> 上次审计时间，3 秒内重复只留一条


def _dedup_audit(ev: dict):
    """同一个人 3 秒内重复打开同一个文件，审计只记一条。

    调用方（如 kb-portal）有时会对同一个文件连发两次 preview 请求，
    审计里就会出现两条一模一样的记录。访问明细 access_log 照常记录（访问量要真实），
    只把审计日志去重。
    """
    if not ev.get("audit"):
        return
    key = (ev.get("emp_no") or "", ev.get("file_key") or "", ev.get("action") or "")
    now = time.time()
    last = _AUDIT_DEDUP.get(key)
    if last is not None and now - last < 3:
        ev["audit"] = False
        return
    if len(_AUDIT_DEDUP) >= 20000:
        _AUDIT_DEDUP.clear()
    _AUDIT_DEDUP[key] = now


def _fill_who(ev: dict):
    """预览页/文件流这些请求没有请求体，姓名部门只能从令牌拿。

    令牌里没有时（旧链接、或调用方本来就没传），用这个缓存按工号补齐，
    否则审计日志里会大量出现"有工号没姓名"。
    """
    emp = (ev.get("emp_no") or "").strip()
    if not emp:
        return
    nm = (ev.get("emp_name") or "").strip()
    dp = (ev.get("dept") or "").strip()
    if nm or dp:
        if len(_WHO) >= 20000:
            _WHO.clear()
        _WHO[emp] = (nm, dp)
        return
    hit = _WHO.get(emp)
    if hit:
        if hit[0]:
            ev["emp_name"] = hit[0]
        if hit[1]:
            ev["dept"] = hit[1]


def record(event: dict):
    """网关主链路调用: 入队即返回, 不阻塞请求。

    429(限流拒绝)不写明细: 限流风暴时拒绝量可达正常量几十倍,
    写明细会直接压垮存储并污染统计口径, 只在实时计数里体现。
    """
    if not config.LOG_DETAIL:
        return
    # 429 默认丢弃（限流风暴时拒绝量可达正常量几十倍）。
    # 例外：主链路按"每分钟×每系统"采样，会带上 sampled=True，只记这一条。
    if event.get("status_code") == 429 and not event.get("sampled"):
        return
    _fill_who(event)
    _dedup_audit(event)
    try:
        q().put_nowait(event)
    except asyncio.QueueFull:
        log.warning("埋点队列已满, 丢弃一条记录")


INSERT_LOG = """
INSERT INTO access_log
(request_id, app_id, emp_no, emp_name, dept, file_key, file_name, file_ext, file_type,
 action, cost_ms, e2e, upstream_ms, cache_hit, is_async, status_code, success, error_msg,
 client_ip, user_agent, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

INSERT_AUDIT = """
INSERT IGNORE INTO audit_log
(request_id, emp_no, emp_name, dept, app_id, file_key, file_url, file_name, file_ext,
 action, result, detail, client_ip, cost_ms, created_at)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def _row_log(e: dict):
    return (e.get("request_id", ""), e.get("app_id", ""), e.get("emp_no", ""),
            e.get("emp_name", ""), e.get("dept", ""), e.get("file_key", ""),
            e.get("file_name", "")[:500], e.get("file_ext", ""), e.get("file_type", ""),
            e.get("action", "preview"), int(e.get("cost_ms", 0)),
            1 if e.get("e2e") else 0,
            int(e.get("upstream_ms", 0)),
            1 if e.get("cache_hit") else 0, 1 if e.get("is_async") else 0,
            int(e.get("status_code", 200)), 1 if e.get("success") else 0,
            (e.get("error_msg") or "")[:250], e.get("client_ip", ""),
            (e.get("user_agent") or "")[:250], e.get("created_at"))


def _row_audit(e: dict):
    if not e.get("audit"):
        return None
    return (e.get("request_id", ""), e.get("emp_no", ""), e.get("emp_name", ""),
            e.get("dept", ""), e.get("app_id", ""), e.get("file_key", ""),
            (e.get("file_url") or "")[:1000], e.get("file_name", "")[:500],
            e.get("file_ext", ""), e.get("action", "preview"),
            e.get("result", "success"), (e.get("detail") or "")[:250],
            e.get("client_ip", ""), int(e.get("cost_ms", 0)), e.get("created_at"))


async def _apply_load_time(batch):
    """用"端到端加载耗时"覆盖接口耗时。

    /preview/url 自己的 cost_ms 只有 0.2~0.4 秒（校验令牌 + 查缓存 + 拼地址），
    用户在预览页里等的却是"转码 + 下载 + 首屏"这十几二十秒。
    预览页可见后会 sendBeacon 上报，loadmetric 会 UPDATE 已落库的行；
    但**上报可能比异步落库还早**（一次预览只花 3 秒，落库批次还没轮到），
    所以这里再兜一次：落库前把 Redis 里已经算好的耗时盖上去。
    """
    if not config.LOAD_METRIC:
        return
    ids = [e.get("request_id") for e in batch if e.get("action") == "preview"]
    if not ids:
        return
    from . import loadmetric
    found = await loadmetric.pending_e2e(ids)
    if not found:
        return
    for e in batch:
        v = found.get(e.get("request_id"))
        if v:
            e["cost_ms"] = int(v)
            e["e2e"] = 1        # 标记"这一条是真实加载耗时"，看板只按它算延迟


async def _flush_loop():
    while _running:
        batch = []
        try:
            item = await asyncio.wait_for(q().get(), timeout=config.COLLECT_FLUSH_INTERVAL)
            batch.append(item)
        except asyncio.TimeoutError:
            pass
        while len(batch) < config.COLLECT_BATCH:
            try:
                batch.append(q().get_nowait())
            except asyncio.QueueEmpty:
                break
        if not batch:
            continue
        try:
            await _apply_load_time(batch)
            await db.executemany(INSERT_LOG, [_row_log(e) for e in batch])
            audits = [r for r in (_row_audit(e) for e in batch) if r]
            if audits:
                await db.executemany(INSERT_AUDIT, audits)
            await _incr_realtime(batch)
        except Exception as exc:
            log.error("埋点落库失败: %s (丢弃 %s 条)", exc, len(batch))


async def _incr_realtime(batch):
    """实时计数: Redis 累加当日与总量, 供看板秒级读取。"""
    try:
        r = await db.get_redis()
        day = time.strftime("%Y-%m-%d")
        pipe = r.pipeline()
        for e in batch:
            app = e.get("app_id", "")
            pipe.incr("rt:day:%s:%s" % (day, app))
            pipe.incr("rt:day:%s:_all" % day)
            pipe.incr("rt:total:%s" % app)
            pipe.incr("rt:total:_all")
            if e.get("file_key"):
                pipe.hincrby("rt:file:%s" % day, e["file_key"], 1)
        for key in ("rt:day:%s:_all" % day, "rt:total:_all"):
            pipe.expire(key, 86400 * 400)
        await pipe.execute()
    except Exception as exc:
        log.warning("实时计数写入失败: %s", exc)


# ---------------------------------------------------------------- 聚合

AGG_DAILY = """
REPLACE INTO stat_daily
(stat_date, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt, async_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE(created_at), app_id, %s, COUNT(*), SUM(success), SUM(1-success),
       SUM(cache_hit), SUM(is_async), SUM(cost_ms), ROUND(AVG(cost_ms)), MAX(cost_ms),
       MIN(cost_ms), COUNT(DISTINCT emp_no), COUNT(DISTINCT NULLIF(file_key, ''))
FROM access_log WHERE created_at >= %s AND created_at < %s GROUP BY DATE(created_at), app_id
"""

AGG_DAILY_ALL = """
REPLACE INTO stat_daily
(stat_date, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt, async_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE(created_at), '_all', 'all', COUNT(*), SUM(success), SUM(1-success),
       SUM(cache_hit), SUM(is_async), SUM(cost_ms), ROUND(AVG(cost_ms)), MAX(cost_ms),
       MIN(cost_ms), COUNT(DISTINCT emp_no), COUNT(DISTINCT NULLIF(file_key, ''))
FROM access_log WHERE created_at >= %s AND created_at < %s GROUP BY DATE(created_at)
"""

AGG_WEEKLY_ALL = """
REPLACE INTO stat_weekly
(stat_week, week_start, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE_FORMAT(stat_date, '%%Y-%%u'), MIN(stat_date), '_all', 'all',
       SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(cache_hit_cnt), SUM(sum_cost_ms),
       ROUND(AVG(avg_cost_ms)), MAX(max_cost_ms), MIN(min_cost_ms),
       MAX(uniq_emp), MAX(uniq_file)
FROM stat_daily WHERE app_id='_all' AND stat_date >= %s AND stat_date < %s
GROUP BY DATE_FORMAT(stat_date, '%%Y-%%u')
"""

AGG_MONTHLY_ALL = """
REPLACE INTO stat_monthly
(stat_month, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE_FORMAT(stat_date, '%%Y-%%m'), '_all', 'all',
       SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(cache_hit_cnt), SUM(sum_cost_ms),
       ROUND(AVG(avg_cost_ms)), MAX(max_cost_ms), MIN(min_cost_ms),
       MAX(uniq_emp), MAX(uniq_file)
FROM stat_daily WHERE app_id='_all' AND stat_date >= %s AND stat_date < %s
GROUP BY DATE_FORMAT(stat_date, '%%Y-%%m')
"""

AGG_FILE = """
REPLACE INTO stat_file_daily
(stat_date, file_key, file_name, file_ext, file_type, view_cnt, uniq_emp, app_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, last_view_at)
SELECT DATE(created_at), file_key, MAX(file_name), MAX(file_ext), MAX(file_type),
       COUNT(*), COUNT(DISTINCT emp_no), COUNT(DISTINCT app_id), SUM(cost_ms),
       ROUND(AVG(cost_ms)), MAX(cost_ms), MIN(cost_ms), MAX(created_at)
FROM access_log WHERE created_at >= %s AND created_at < %s AND file_key <> ''
GROUP BY DATE(created_at), file_key
"""

AGG_HIST = """
REPLACE INTO stat_latency_hist (stat_date, app_id, bucket_lo, bucket_hi, cnt)
SELECT DATE(created_at), app_id,
       CASE {lo} END, CASE {hi} END, COUNT(*)
FROM access_log WHERE created_at >= %s AND created_at < %s
GROUP BY 1, 2, 3, 4
"""

HIST_CASE_LO = " ".join(
    "WHEN cost_ms >= %d AND cost_ms < %d THEN %d" % (lo, hi, lo) for lo, hi in BUCKETS)
HIST_CASE_HI = " ".join(
    "WHEN cost_ms >= %d AND cost_ms < %d THEN %d" % (lo, hi, hi) for lo, hi in BUCKETS)

AGG_WEEKLY = """
REPLACE INTO stat_weekly
(stat_week, week_start, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE_FORMAT(stat_date, '%%Y-%%u'), MIN(stat_date), app_id, file_type,
       SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(cache_hit_cnt), SUM(sum_cost_ms),
       ROUND(AVG(avg_cost_ms)), MAX(max_cost_ms), MIN(min_cost_ms),
       MAX(uniq_emp), MAX(uniq_file)
FROM stat_daily WHERE stat_date >= %s AND stat_date < %s GROUP BY DATE_FORMAT(stat_date, '%%Y-%%u'), app_id, file_type
"""

AGG_MONTHLY = """
REPLACE INTO stat_monthly
(stat_month, app_id, file_type, total_cnt, success_cnt, fail_cnt, cache_hit_cnt,
 sum_cost_ms, avg_cost_ms, max_cost_ms, min_cost_ms, uniq_emp, uniq_file)
SELECT DATE_FORMAT(stat_date, '%%Y-%%m'), app_id, file_type,
       SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(cache_hit_cnt), SUM(sum_cost_ms),
       ROUND(AVG(avg_cost_ms)), MAX(max_cost_ms), MIN(min_cost_ms),
       MAX(uniq_emp), MAX(uniq_file)
FROM stat_daily WHERE stat_date >= %s AND stat_date < %s GROUP BY DATE_FORMAT(stat_date, '%%Y-%%m'), app_id, file_type
"""

AGG_TOTAL = """
REPLACE INTO stat_total
(app_id, total_cnt, success_cnt, fail_cnt, sum_cost_ms, max_cost_ms, min_cost_ms,
 first_at, last_at)
SELECT app_id, SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(sum_cost_ms),
       MAX(max_cost_ms), MIN(min_cost_ms), MIN(stat_date), MAX(stat_date)
FROM stat_daily GROUP BY app_id
"""

AGG_TOTAL_ALL = """
REPLACE INTO stat_total
(app_id, total_cnt, success_cnt, fail_cnt, sum_cost_ms, max_cost_ms, min_cost_ms,
 first_at, last_at)
SELECT '_all', SUM(total_cnt), SUM(success_cnt), SUM(fail_cnt), SUM(sum_cost_ms),
       MAX(max_cost_ms), MIN(min_cost_ms), MIN(stat_date), MAX(stat_date)
FROM stat_daily WHERE app_id <> '_all'
"""


async def aggregate_once(days_back: int = 2):
    """增量聚合: 覆盖最近 N 天(幂等, 可重复执行)。"""
    import datetime
    end = datetime.date.today() + datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days_back)
    s, e = start.isoformat(), end.isoformat()
    try:
        await db.execute(AGG_DAILY, ("all", s, e))
        await db.execute(AGG_DAILY_ALL, (s, e))
        await db.execute(AGG_FILE, (s, e))
        await db.execute(AGG_HIST.format(lo=HIST_CASE_LO, hi=HIST_CASE_HI), (s, e))
        week_start = start - datetime.timedelta(days=start.weekday() + 7)
        await db.execute(AGG_WEEKLY, (week_start.isoformat(), e))
        await db.execute(AGG_WEEKLY_ALL, (week_start.isoformat(), e))
        month_start = start.replace(day=1)
        await db.execute(AGG_MONTHLY, (month_start.isoformat(), e))
        await db.execute(AGG_MONTHLY_ALL, (month_start.isoformat(), e))
        await db.execute(AGG_TOTAL)
        await db.execute(AGG_TOTAL_ALL)
    except Exception as exc:
        log.error("聚合失败: %s", exc)


async def _aggregate_loop():
    # 0 表示关闭。必须在这里挡住：`await asyncio.sleep(0)` 不会等待，
    # 循环会退化成忙轮询，把 9 条聚合 SQL 连成一片把 CPU 打满。
    if config.AGGREGATE_INTERVAL <= 0:
        log.info("后台聚合已关闭（AGGREGATE_INTERVAL=0），需要时手动跑 clean_metrics.py")
        return
    while _running:
        await asyncio.sleep(config.AGGREGATE_INTERVAL)
        try:
            await aggregate_once()
        except Exception as exc:
            log.error("聚合任务异常: %s", exc)


async def purge_old():
    """按 RETENTION_DAYS 清理明细表(默认保留 180 天)。

    审计日志和访问明细是"能回溯半年"的依据，但也不能无限增长：
    每天分批删掉 N 天前的数据（每批 2 万条，避免长事务锁表）。
    聚合表 stat_daily/weekly/monthly 体积小，长期保留不删。

    `preview_task` 单独按 PREVIEW_TASK_KEEP_DAYS（默认 90 天）清理：
    它记的是"这次转码任务"的运行时状态，不是审计记录 —— 谁看了什么文件在
    access_log / audit_log 里。不清理的话它只增不减（实测日均几百条，
    10-22 至今 3144 条），而 health 页的「打不开的文件」要 GROUP BY 全表，
    时间越久越慢。
    """
    days = config.RETENTION_DAYS
    if days > 0:
        for table in ("access_log", "audit_log"):
            total = 0
            for _ in range(100):
                n = await db.execute(
                    "DELETE FROM %s WHERE created_at < DATE_SUB(CURDATE(), INTERVAL %%s DAY)"
                    " LIMIT 20000" % table, (days,))
                if not n:
                    break
                total += n
            if total:
                log.info("已清理 %s 中 %d 天前的记录 %d 条", table, days, total)

    keep = config.PREVIEW_TASK_KEEP_DAYS
    if keep > 0:
        total = 0
        for _ in range(50):
            # 正在排队/转换的绝不删（正常不会这么久，但别赌）
            n = await db.execute(
                "DELETE FROM preview_task WHERE created_at < DATE_SUB(CURDATE(),"
                " INTERVAL %s DAY) AND status NOT IN ('queued','converting') LIMIT 20000",
                (keep,))
            if not n:
                break
            total += n
        if total:
            log.info("已清理 preview_task 中 %d 天前的任务记录 %d 条", keep, total)


async def _purge_loop():
    # 启动后先等 5 分钟避开重启高峰，之后每 6 小时检查一次（DELETE 本身是幂等的）
    await asyncio.sleep(300)
    while _running:
        try:
            await purge_old()
        except Exception as exc:
            log.error("清理历史数据失败: %s", exc)
        await asyncio.sleep(6 * 3600)


async def _warm_who():
    """启动时把历史工号→姓名部门载入内存，重启后也能补齐空姓名。"""
    try:
        rows = await db.query(
            "SELECT emp_no, MAX(emp_name) emp_name, MAX(dept) dept FROM access_log"
            " WHERE emp_name <> '' GROUP BY emp_no")
        for r in rows:
            _WHO[r["emp_no"]] = (r["emp_name"] or "", r["dept"] or "")
        log.info("已载入 %d 个工号的姓名部门缓存", len(_WHO))
    except Exception as exc:
        log.warning("载入工号缓存失败: %s", exc)


async def start():
    global _running
    _running = True
    asyncio.create_task(_warm_who())
    asyncio.create_task(_flush_loop())
    asyncio.create_task(_aggregate_loop())
    asyncio.create_task(_purge_loop())
    log.info("埋点与聚合后台任务已启动")


async def stop():
    global _running
    _running = False
    await asyncio.sleep(config.COLLECT_FLUSH_INTERVAL + 1)
