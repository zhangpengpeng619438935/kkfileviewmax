# -*- coding: utf-8 -*-
"""服务告警：只看"服务扛不扛得住"，不看"哪个文件打不开"。

**两类信息必须分开**

| 类别 | 例子 | 记在哪 | 看板页 |
|---|---|---|---|
| 预览失败原因 | 源文件 404、文档加密、签名过期、单次被限流 | access_log（每次一条） | 「预览失败」 |
| 服务告警 | 多系统同时被限流、排队超 5 分钟、CPU 打满、磁盘快满 | alert_log（每个事件一条生命周期） | 「服务告警」 |

失败原因一天几千条、每条只描述"这一次"，而告警是**状态**：从触发到恢复只有一个
生命周期，要记首次/末次/峰值/持续时长。混在一张表里没法回答"这次告警持续了多久"。

**10 条规则（全部是"容量/压力"维度）**

| kind | 含义 | 触发条件 | 级别 |
|---|---|---|---|
| rate_multi    | 多系统同时限流 | 1 分钟内有 ≥3 个系统账号被 429 | warn |
| rate_storm    | 单系统限流风暴 | 单系统 5 分钟内被 429 ≥300 次 | critical |
| queue_timeout | 预览排队超时   | 有任务排队 ≥5 分钟仍未开始转换 | critical |
| load_timeout  | 文档加载超时   | 有打开请求端到端 ≥5 分钟 | critical |
| cpu_high      | CPU 逼近限额   | 1 分钟峰值 ≥8 核的 90%（7.2 核） | critical |
| mem_high      | 内存逼近上限   | ≥10GB 的 90%（9GB） | critical |
| disk_low      | 磁盘空间不足   | 缓存盘可用 <20GB | critical |
| cache_big     | 缓存目录超阈值 | 预览缓存 ≥160GB（上限 200GB 的 80%） | warn |
| queue_backlog | 转换队列积压   | 排队 ≥20 个且持续 ≥5 分钟 | warn |
| engine_error  | 引擎/转换异常  | 5 分钟内 5xx 占比 ≥20%，或转换失败率 ≥30%（样本 ≥10） | critical |

**为什么限流计数要放 Redis**：access_log 里的 429 是**采样**的（同一分钟同一系统只留
一条，否则限流风暴会把存储打爆），条数完全不能用来判断"是不是很多人在被拒"。
所以 ratelimit 拒绝时直接 HINCRBY 到一个按分钟分桶的 hash。

**为什么要有选主**：uvicorn 起了 2 个 worker，两个进程会同时评估规则。
告警表不是幂等的（不像聚合表的 ON DUPLICATE KEY），双写会出现两条一模一样的告警。
用 Redis 抢一个带过期的 leader 键，只有 leader 评估。
"""
import asyncio
import collections
import logging
import os
import time

from . import config, db

log = logging.getLogger("alerts")

LEADER_KEY = "kk:alert:leader"
LEADER_TTL = 180            # 秒；进程挂了最多 3 分钟后由另一个 worker 接手

# CPU/内存采样：20 秒一个点，保留 6 个（覆盖最近 2 分钟）。
# 为什么不在评估时现取：容器 CPU 是累计计数器，两次采样才知道"用了多少核"。
_SAMPLE_SEC = 20
_cpu_pts = collections.deque(maxlen=6)      # (ts, usage_usec)
_mem_pts = collections.deque(maxlen=6)      # (ts, bytes)
_peak = {"cpu": 0.0, "mem": 0.0}

_running = False
_pid = str(os.getpid())


# ------------------------------------------------------------------ 表结构
DDL = """
CREATE TABLE IF NOT EXISTS alert_log (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  kind        VARCHAR(32)  NOT NULL COMMENT '规则标识, 如 cpu_high',
  level       VARCHAR(10)  NOT NULL DEFAULT 'warn' COMMENT 'warn/critical',
  title       VARCHAR(64)  NOT NULL DEFAULT '' COMMENT '中文名',
  target      VARCHAR(128) NOT NULL DEFAULT '' COMMENT '作用对象: 全局/某系统',
  value       DOUBLE       NOT NULL DEFAULT 0  COMMENT '最近一次触发时的值',
  peak        DOUBLE       NOT NULL DEFAULT 0  COMMENT '持续期间的峰值',
  threshold   DOUBLE       NOT NULL DEFAULT 0  COMMENT '阈值',
  unit        VARCHAR(16)  NOT NULL DEFAULT '' COMMENT '核/GB/次/个/秒',
  detail      VARCHAR(512) NOT NULL DEFAULT '',
  hits        INT UNSIGNED NOT NULL DEFAULT 1  COMMENT '持续期间被确认的轮数',
  status      VARCHAR(12)  NOT NULL DEFAULT 'open' COMMENT 'open/resolved',
  first_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  resolved_at DATETIME     NULL,
  PRIMARY KEY (id),
  KEY idx_status (status, last_at),
  KEY idx_kind_time (kind, first_at),
  KEY idx_first (first_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='服务告警事件(压力维度)'
"""


async def init_table():
    try:
        await db.execute(DDL)
    except Exception as exc:
        log.warning("告警表初始化失败(不影响预览): %s", exc)


# ------------------------------------------------------------------ 资源采样
def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def cpu_usage_usec() -> int:
    """cgroup v2 的累计 CPU 时间（微秒）；v1 回退到 cpuacct（纳秒）。"""
    for line in _read("/sys/fs/cgroup/cpu.stat").splitlines():
        if line.startswith("usage_usec"):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    v1 = _read("/sys/fs/cgroup/cpuacct/cpuacct.usage")
    if v1.isdigit():
        return int(v1) // 1000
    return 0


def _inactive_file() -> int:
    """cgroup 里"可回收的文件缓存"字节数。"""
    for line in _read("/sys/fs/cgroup/memory.stat").splitlines():
        if line.startswith("inactive_file "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    # cgroup v1 叫 total_inactive_file
    for line in _read("/sys/fs/cgroup/memory/memory.stat").splitlines():
        if line.startswith("total_inactive_file "):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                return 0
    return 0


def mem_bytes() -> int:
    """容器实际占用的内存（**扣除可回收的 page cache**）。

    原来直接用 `memory.current`，而它包含读写文件产生的 page cache ——
    预览服务每转一个文件都要读源、写产物，cache 很容易堆到几 GB。
    那部分在内核有压力时会自动回收，并不代表"快 OOM 了"。
    实测 11-04 16:06 报过 9.98GB / 10GB 的 critical，而同期进程真实占用是
    1GB 量级、容器限额 15GB —— 纯粹是 cache 撑起来的假数字。

    现在减去 inactive_file，与 `docker stats` 的 MemUsage 口径一致，
    运维在两边看到的数字不会打架。
    """
    v = _read("/sys/fs/cgroup/memory.current")
    if v.isdigit():
        return max(0, int(v) - _inactive_file())
    v = _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if v.isdigit():
        return max(0, int(v) - _inactive_file())
    return 0


def sample_once():
    now = time.time()
    u = cpu_usage_usec()
    if u:
        _cpu_pts.append((now, u))
    m = mem_bytes()
    if m:
        _mem_pts.append((now, m))


def peak_in(window: float = 60.0):
    """最近 window 秒内的 CPU 峰值（核）与内存峰值（字节）。"""
    now = time.time()
    cores = 0.0
    pts = [(t, u) for t, u in _cpu_pts if now - t <= window + _SAMPLE_SEC]
    for (t1, u1), (t2, u2) in zip(pts, pts[1:]):
        dt = t2 - t1
        if dt > 0 and u2 >= u1:
            cores = max(cores, (u2 - u1) / 1e6 / dt)
    mem = 0
    for t, m in _mem_pts:
        if now - t <= window:
            mem = max(mem, m)
    return cores, mem


# ------------------------------------------------------------------ 各规则取数
async def _rate_counts(minutes: int):
    """从 Redis 取最近 N 分钟被 429 的次数：{app_id: 次数}。

    键是 kk:rlcnt:<YYYYMMDDHHMM> 的 hash（field=app_id），见 ratelimit._bump。
    """
    r = await db.get_redis()
    keys = []
    now = time.time()
    for i in range(minutes):
        keys.append("kk:rlcnt:" + time.strftime("%Y%m%d%H%M",
                                                time.localtime(now - i * 60)))
    out = {}
    try:
        pipe = r.pipeline()
        for k in keys:
            pipe.hgetall(k)
        hashes = await pipe.execute()
    except Exception:
        return out
    for h in hashes or []:
        if not isinstance(h, dict):
            continue
        for app_id, n in h.items():
            if isinstance(app_id, bytes):
                app_id = app_id.decode("utf-8", "replace")
            try:
                out[app_id] = out.get(app_id, 0) + int(n)
            except (TypeError, ValueError):
                continue
    return out


async def _queue_stats(wait_min: int):
    """排队超时个数 + 当前队列深度 + 最久排队秒数。"""
    row = await db.query_one(
        "SELECT SUM(status='queued') q, SUM(status='queued' AND"
        " created_at < NOW() - INTERVAL %s MINUTE) slow,"
        " MAX(IF(status='queued', TIMESTAMPDIFF(SECOND, created_at, NOW()), 0)) oldest"
        " FROM preview_task WHERE updated_at > NOW() - INTERVAL 60 MINUTE",
        (wait_min,))
    return {"queued": int((row or {}).get("q") or 0),
            "slow": int((row or {}).get("slow") or 0),
            "oldest": int((row or {}).get("oldest") or 0)}


async def _slow_loads(wait_min: int):
    """最近 15 分钟内端到端耗时超过阈值、且是真实加载耗时（e2e=1）的请求数。"""
    row = await db.query_one(
        "SELECT COUNT(*) c, MAX(cost_ms) mx FROM access_log"
        " WHERE created_at > NOW() - INTERVAL 15 MINUTE"
        "   AND e2e=1 AND cost_ms >= %s", (wait_min * 60 * 1000,))
    return int((row or {}).get("c") or 0), int((row or {}).get("mx") or 0)


async def _convert_fail(minutes: int = 5):
    """最近 N 分钟的转换结果：失败率、样本数、5xx 请求占比。"""
    row = await db.query_one(
        "SELECT SUM(status='failed') f, SUM(status='ready') r FROM preview_task"
        " WHERE updated_at > NOW() - INTERVAL %s MINUTE", (minutes,))
    f = int((row or {}).get("f") or 0)
    ok = int((row or {}).get("r") or 0)
    tot = f + ok
    http = await db.query_one(
        "SELECT SUM(status_code >= 500) e, COUNT(*) c FROM access_log"
        " WHERE created_at > NOW() - INTERVAL %s MINUTE", (minutes,))
    e = int((http or {}).get("e") or 0)
    c = int((http or {}).get("c") or 0)
    return {"failed": f, "ready": ok, "convert_rate": (f / tot if tot else 0.0),
            "samples": tot, "err": e, "reqs": c,
            "http_rate": (e / c if c else 0.0)}


# ------------------------------------------------------------------ 规则评估
async def evaluate() -> list:
    """返回本轮命中的告警列表（未命中的由 apply 负责 resolve）。"""
    from . import metrics, sheet
    hits = []
    cores, mem = peak_in()
    cfg = config

    # 1) 多系统同时限流
    per_app = await _rate_counts(1)
    apps = {k: v for k, v in per_app.items() if v > 0 and k != "__global__"}
    if len(apps) >= cfg.ALERT_RL_APPS:
        top = sorted(apps.items(), key=lambda kv: -kv[1])[:5]
        hits.append(dict(
            kind="rate_multi", level="warn", title="多系统同时限流",
            target="全局", value=len(apps), threshold=cfg.ALERT_RL_APPS,
            unit="个系统",
            detail="同一分钟被限流的系统: " + "、".join("%s(%d次)" % kv for kv in top)))

    # 2) 单系统限流风暴
    per_app5 = await _rate_counts(5)
    for app_id, n in per_app5.items():
        if n >= cfg.ALERT_RL_COUNT:
            hits.append(dict(
                kind="rate_storm", level="critical", title="单系统限流风暴",
                target=app_id, value=n, threshold=cfg.ALERT_RL_COUNT,
                unit="次/5分钟",
                detail="系统 %s 在 5 分钟内被限流 %d 次，检查配额或调用方是否有重试风暴"
                       % (app_id, n)))

    # 3) 预览排队超时
    qs = await _queue_stats(cfg.ALERT_WAIT_MIN)
    # 值与阈值同量纲：都是"分钟"。写成"超时任务个数"会出现"当前值 25 个 / 阈值 5 个"
    # 这种读不懂的组合（5 个是什么？），而用户的诉求本来就叫"排队超过 5 分钟"。
    if qs["slow"] > 0:
        mins = max(1, qs["oldest"] // 60)
        hits.append(dict(
            kind="queue_timeout", level="critical", title="预览排队超时",
            target="全局", value=mins, threshold=cfg.ALERT_WAIT_MIN,
            unit="分钟",
            detail="最久已等 %d 分钟（%d 个任务排队超过 %d 分钟仍未开始转换）"
                   % (mins, qs["slow"], cfg.ALERT_WAIT_MIN)))

    # 4) 文档加载超时
    slow, mx = await _slow_loads(cfg.ALERT_WAIT_MIN)
    if slow > 0:
        mins = max(1, mx // 60000)
        hits.append(dict(
            kind="load_timeout", level="critical", title="文档加载超时",
            target="全局", value=mins, threshold=cfg.ALERT_WAIT_MIN,
            unit="分钟",
            detail="最慢一次打开用了 %d 分钟（%d 次超过 %d 分钟）"
                   % (mins, slow, cfg.ALERT_WAIT_MIN)))

    # 4b) 页面始终没出内容（探针跑满上限）
    # 上面那条看的是"慢但出来了"（e2e=1 且 ≥5 分钟）。可探针上限只有 60/120 秒，
    # 一旦到点报超时，样本就不再打 e2e 标记（否则 120 秒会顶住 p99，见 loadmetric），
    # 于是"用户盯着白屏等了两分钟"这件事在告警里完全消失了。
    # 这里按超时计数单独兜一条：10 分钟内 >5 次就说明有一批文件打不开。
    try:
        from . import loadmetric as _lm
        to_cnt = await _lm.timeout_count()
        recent = await db.query_one(
            "SELECT COUNT(*) c FROM access_log WHERE created_at > NOW() - INTERVAL 10 MINUTE"
            " AND action='preview' AND e2e=0 AND cost_ms BETWEEN 110000 AND 130000")
        recent = int((recent or {}).get("c") or 0)
    except Exception:
        to_cnt, recent = 0, 0
    if recent >= 5 or to_cnt >= 20:
        hits.append(dict(
            kind="load_stuck", level="critical", title="页面长时间不出内容",
            target="全局", value=max(to_cnt, recent), threshold=5, unit="次",
            detail="今日 %d 次、最近 10 分钟 %d 次打开后探针等满上限仍未看到内容"
                   "（多为超大表格/装配体或前端渲染卡住）" % (to_cnt, recent)))

    # 5) CPU 逼近限额
    if cores >= cfg.ALERT_CPU_CORES * 0.9:
        hits.append(dict(
            kind="cpu_high", level="critical", title="CPU 逼近限额",
            target="网关容器", value=round(cores, 2),
            threshold=round(cfg.ALERT_CPU_CORES * 0.9, 2), unit="核",
            detail="1 分钟峰值 %.2f 核（容器上限 %g 核），转换会开始变慢"
                   % (cores, cfg.ALERT_CPU_CORES)))

    # 6) 内存逼近上限
    mem_gb = mem / 1073741824.0
    if mem_gb >= cfg.ALERT_MEM_GB * 0.9:
        hits.append(dict(
            kind="mem_high", level="critical", title="内存逼近上限",
            target="网关容器", value=round(mem_gb, 2),
            threshold=round(cfg.ALERT_MEM_GB * 0.9, 2), unit="GB",
            detail="当前 %.2f GB（容器上限 %g GB），再涨会被 OOM Kill"
                   % (mem_gb, cfg.ALERT_MEM_GB)))

    # 7) 磁盘空间不足
    free_gb = metrics.disk_free_bytes(sheet.SHEET_DIR) / 1073741824.0
    if free_gb and free_gb < cfg.ALERT_DISK_FREE_GB:
        hits.append(dict(
            kind="disk_low", level="critical", title="磁盘空间不足",
            target="缓存盘", value=round(free_gb, 1),
            threshold=cfg.ALERT_DISK_FREE_GB, unit="GB 可用",
            detail="可用 %.1f GB，低于 %g GB 后新转换会写失败"
                   % (free_gb, cfg.ALERT_DISK_FREE_GB)))

    # 8) 缓存目录超阈值
    cache_gb = metrics.cache_dir_bytes(sheet.SHEET_DIR) / 1073741824.0
    if cache_gb >= cfg.ALERT_CACHE_GB:
        hits.append(dict(
            kind="cache_big", level="warn", title="缓存目录超阈值",
            target="预览缓存", value=round(cache_gb, 1),
            threshold=cfg.ALERT_CACHE_GB, unit="GB",
            detail="已占 %.1f GB，接近 %d GB 上限，gc_cache 会在 03:30 回收"
                   % (cache_gb, config.ARCHIVE_CACHE_TOTAL_MB // 1024)))

    # 9) 转换队列积压
    if qs["queued"] >= cfg.ALERT_QUEUE_MAX:
        hits.append(dict(
            kind="queue_backlog", level="warn", title="转换队列积压",
            target="全局", value=qs["queued"], threshold=cfg.ALERT_QUEUE_MAX,
            unit="个",
            detail="当前 %d 个任务排队（阈值 %d），并发上限 %d"
                   % (qs["queued"], cfg.ALERT_QUEUE_MAX, config.MAX_CONCURRENT_CONVERT)))

    # 10) 引擎/转换异常
    cf = await _convert_fail(5)
    if (cf["samples"] >= 10 and cf["convert_rate"] >= 0.3) or \
            (cf["reqs"] >= 20 and cf["http_rate"] >= 0.2):
        hits.append(dict(
            kind="engine_error", level="critical", title="引擎/转换异常",
            target="预览引擎",
            value=round(max(cf["convert_rate"], cf["http_rate"]) * 100, 1),
            threshold=30.0, unit="%",
            detail="近 5 分钟转换失败 %d/%d、5xx %d/%d，检查 LibreOffice 与引擎容器"
                   % (cf["failed"], cf["samples"], cf["err"], cf["reqs"])))

    return hits


# ---------------------------------------------------------------- 推送
NOTIFY_KEY = "kk:alert:notified:%s|%s"     # kind|target → 上次触发通知时间


def _payload(text: str) -> dict:
    """按通道组装请求体。企业微信与钉钉的 markdown 字段名不同，别混用。"""
    kind = config.ALERT_WEBHOOK_KIND
    if kind == "dingtalk":
        return {"msgtype": "markdown",
                "markdown": {"title": "预览服务告警", "text": text}}
    if kind == "raw":
        return {"text": text}
    # 企业微信群机器人
    return {"msgtype": "markdown", "markdown": {"content": text}}


async def _post(text: str) -> bool:
    """推送一条。任何异常都吞掉 —— 告警落库才是主流程，推送是尽力而为。"""
    if not config.ALERT_WEBHOOK:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=6.0) as cli:
            r = await cli.post(config.ALERT_WEBHOOK, json=_payload(text))
        # 企微/钉钉都是 HTTP 200 + body 里 errcode/errcode 非 0 表示失败
        if r.status_code >= 300:
            log.warning("告警推送失败 HTTP %s: %s", r.status_code, r.text[:160])
            return False
        try:
            body = r.json()
        except Exception:
            return True
        code = body.get("errcode", body.get("code", 0))
        if code not in (0, "0", None):
            log.warning("告警推送被拒 errcode=%s %s", code,
                        str(body.get("errmsg", ""))[:120])
            return False
        return True
    except Exception as exc:
        log.warning("告警推送异常: %s", exc)
        return False


def _fmt(h: dict, header: str) -> str:
    return ("**%s**\n"
            "> 规则：%s\n"
            "> 对象：%s\n"
            "> 当前：%s%s（阈值 %s%s）\n"
            "> %s") % (header, h.get("title", ""), h.get("target", ""),
                       h.get("value", ""), h.get("unit", ""),
                       h.get("threshold", ""), h.get("unit", ""),
                       h.get("detail", ""))


async def _notify_new(hits: list):
    """把同一轮新开的告警合并成一条消息，并按 key 做静默期。

    不合并的话，一次抖动触发 5 条规则就是 5 条消息；不静默的话，一个持续
    3 小时的告警会每 60 秒推一次（评估周期），等于把群刷满。
    """
    if not config.ALERT_WEBHOOK or not hits:
        return
    r = await db.get_redis()
    fresh = []
    for h in hits:
        k = NOTIFY_KEY % (h["kind"], h["target"])
        try:
            got = await r.set(k, int(time.time()), nx=True, ex=config.ALERT_NOTIFY_GAP)
        except Exception:
            got = True                      # Redis 不可用时不静默，宁可多推
        if got:
            fresh.append(h)
    if not fresh:
        return
    if len(fresh) == 1:
        text = _fmt(fresh[0], "🔴 预览服务告警")
    else:
        text = "🔴 **预览服务告警（%d 条）**\n\n" % len(fresh)
        text += "\n\n".join(_fmt(h, "%d. %s" % (i + 1, h.get("title", "")))
                            for i, h in enumerate(fresh))
    await _post(text)


async def _notify_recovered(rows: list):
    if not config.ALERT_WEBHOOK or not rows:
        return
    if len(rows) == 1:
        r0 = rows[0]
        text = ("✅ **预览服务告警已恢复**\n\n> 规则：%s\n> 对象：%s")
        text = text % (r0.get("title", ""), r0.get("target", ""))
    else:
        text = "✅ **预览服务告警已恢复（%d 条）**\n\n" % len(rows)
        text += "\n".join("> %s · %s" % (x.get("title", ""), x.get("target", ""))
                         for x in rows)
    await _post(text)


# 本模块自己产出的告警类型。`apply()` 只能恢复这些 —— 别的来源（目前是
# tools/cron_run.py 写进来的 kind='cron'）由它们自己负责 resolve。
# 不隔离的话会出这种事：cron 记了一条"备份失败"，60 秒后本轮评估没命中它，
# 直接被标成"已恢复"，告警在看板上一闪而过 —— 等于没记。
# 实测：造一条 open 的 cron 告警，apply([]) 之后 status 立刻变成 resolved。
OWN_KINDS = frozenset((
    "rate_multi", "rate_storm", "queue_timeout", "load_timeout", "load_stuck",
    "cpu_high", "mem_high", "disk_low", "cache_big", "queue_backlog", "engine_error",
))


# ------------------------------------------------------------------ 落库
async def apply(hits: list):
    """命中 → 新开或更新；本轮没再命中的 open 记录 → 置为已恢复。"""
    seen = set()
    opened = []
    for h in hits:
        key = (h["kind"], h["target"])
        seen.add(key)
        row = await db.query_one(
            "SELECT id, peak FROM alert_log WHERE kind=%s AND target=%s"
            " AND status='open' ORDER BY id DESC LIMIT 1", key)
        if row:
            await db.execute(
                "UPDATE alert_log SET last_at=NOW(), hits=hits+1, value=%s,"
                " peak=GREATEST(peak, %s), detail=%s WHERE id=%s",
                (h["value"], h["value"], h["detail"][:512], row["id"]))
        else:
            await db.execute(
                "INSERT INTO alert_log (kind, level, title, target, value, peak,"
                " threshold, unit, detail, hits, status, first_at, last_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,'open',NOW(),NOW())",
                (h["kind"], h["level"], h["title"], h["target"], h["value"],
                 h["value"], h["threshold"], h["unit"], h["detail"][:512]))
            log.warning("服务告警触发 [%s] %s: %s", h["level"], h["title"], h["detail"])
            opened.append(h)

    # 只回看自己管的类型；cron 等外部来源的 open 记录不在这里收尾。
    # 把本轮命中的 kind 也并进来：万一新增了规则却忘了加进 OWN_KINDS，
    # 至少它在命中期间能被正常更新和恢复，不会卡成永久 open。
    kinds = sorted(OWN_KINDS | {h["kind"] for h in hits})
    ph = ",".join(["%s"] * len(kinds))
    opens = await db.query(
        "SELECT id, kind, target, title FROM alert_log WHERE status='open'"
        " AND kind IN (" + ph + ")", tuple(kinds))
    recovered = []
    for r in opens:
        if (r["kind"], r["target"]) not in seen:
            await db.execute(
                "UPDATE alert_log SET status='resolved', resolved_at=NOW(),"
                " last_at=NOW() WHERE id=%s", (r["id"],))
            log.info("服务告警恢复 %s(%s)", r["title"], r["target"])
            recovered.append(r)

    # 推送放在所有落库之后：即使推送挂了，告警记录也是完整的
    await _notify_new(opened)
    await _notify_recovered(recovered)


async def _leader() -> bool:
    """只在 leader worker 上评估。抢不到就等下一个周期。"""
    try:
        r = await db.get_redis()
        if await r.set(LEADER_KEY, _pid, nx=True, ex=LEADER_TTL):
            return True
        cur = await r.get(LEADER_KEY)
        if isinstance(cur, bytes):
            cur = cur.decode()
        if cur == _pid:
            await r.expire(LEADER_KEY, LEADER_TTL)
            return True
    except Exception:
        return False
    return False


async def _tick():
    if not await _leader():
        return
    hits = await evaluate()
    await apply(hits)


# ------------------------------------------------------------------ 循环
async def _sample_loop():
    while _running:
        try:
            sample_once()
        except Exception as exc:
            log.debug("采样失败: %s", exc)
        await asyncio.sleep(_SAMPLE_SEC)


async def _eval_loop():
    # 先等两个采样点，否则 CPU 核数算不出来（首轮必然是 0）
    await asyncio.sleep(_SAMPLE_SEC + 2)
    while _running:
        try:
            await _tick()
        except Exception as exc:
            log.warning("告警评估失败: %s", exc)
        await asyncio.sleep(max(30, config.ALERT_INTERVAL))


async def _purge_loop():
    while _running:
        try:
            await db.execute(
                "DELETE FROM alert_log WHERE first_at < NOW() - INTERVAL %s DAY"
                " LIMIT 2000", (config.RETENTION_DAYS,))
        except Exception:
            pass
        await asyncio.sleep(3600 * 6)


async def start():
    global _running
    if not config.ALERT_ENABLED:
        log.info("服务告警已关闭(ALERT_ENABLED=0)")
        return
    _running = True
    await init_table()
    sample_once()                 # 立刻留一个基准点
    asyncio.create_task(_sample_loop())
    asyncio.create_task(_eval_loop())
    asyncio.create_task(_purge_loop())
    log.info("服务告警采集已启动: 间隔 %ds, 阈值 CPU %d 核 / 内存 %d GB",
             config.ALERT_INTERVAL, config.ALERT_CPU_CORES, config.ALERT_MEM_GB)


async def stop():
    global _running
    _running = False


async def snapshot():
    """给 /health 用：当前未恢复的告警数与最近一条。"""
    try:
        row = await db.query_one(
            "SELECT COUNT(*) c FROM alert_log WHERE status='open'")
        last = await db.query_one(
            "SELECT kind, title, level, first_at FROM alert_log"
            " WHERE status='open' ORDER BY id DESC LIMIT 1")
        return {"open": int((row or {}).get("c") or 0),
                "last": (last or {}).get("title") or ""}
    except Exception:
        return {"open": 0, "last": ""}
