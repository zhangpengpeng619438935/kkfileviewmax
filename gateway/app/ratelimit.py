import time
import uuid

from fastapi import HTTPException

from . import config, db


async def _allow(key: str, limit: int) -> bool:
    """滑动窗口：只数最近 1 秒内的请求。

    原来是 `rl:<key>:<自然秒>` 的固定窗口（INCR + EXPIRE）。跨秒边界时，
    第 59 秒末尾的 N 次和第 60 秒开头的 N 次落在两个窗口里，实际能打到 2×QPS。
    改成 ZSET 后窗口跟着"现在"滑动，语义就是"最近 1 秒不超过 limit"。

    代价是每个 key 存 1 秒内的成员（全局 120 QPS → 最多约 240 个，2 秒过期）。
    """
    if limit <= 0:
        return True
    r = await db.get_redis()
    now = time.time()
    rk = "rl:" + key
    # member 必须唯一：同一微秒的两个请求用同一个 score 会互相覆盖，少算一次
    member = "%.6f|%s" % (now, uuid.uuid4().hex[:6])
    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(rk, 0, now - 1.0)
        pipe.zadd(rk, {member: now})
        pipe.zcard(rk)
        pipe.expire(rk, 2)
        cnt = (await pipe.execute())[2]
    except Exception:
        return True   # Redis 异常时放行, 避免统计组件拖垮主链路
    return cnt <= limit


def is_system_emp(emp_no: str) -> bool:
    """系统批量任务工号（SYS_*）。它们不代表自然人，单独用 appId 配额。"""
    prefix = config.SYS_EMP_PREFIX
    return bool(prefix) and (emp_no or "").upper().startswith(prefix)


async def _bump(app_id: str):
    """记一次"被限流"，按分钟分桶、field 是系统 appId。

    为什么不用 access_log 数：那里的 429 是**采样**的（同一分钟同一系统只留一条，
    否则限流风暴会把存储打爆），条数根本反映不出"有多少人在被拒"。
    服务告警要靠这个计数判断"3 个系统同时被限流""某系统 5 分钟被拒 300 次"。

    失败不影响主链路：计数只是观测，宁可少一条告警，不能因为 Redis 抖动把请求也搞挂。
    """
    try:
        r = await db.get_redis()
        k = "kk:rlcnt:" + time.strftime("%Y%m%d%H%M")
        pipe = r.pipeline()
        pipe.hincrby(k, app_id or "_unknown", 1)
        pipe.expire(k, 900)
        await pipe.execute()
    except Exception:
        pass


async def check(app_id: str, emp_no: str, app_qps: int | None = None):
    if not await _allow("g", config.GLOBAL_QPS):
        await _bump(app_id)
        raise HTTPException(status_code=429, detail="全局流量超限, 请稍后重试")
    if not await _allow("a:%s" % app_id, app_qps or config.DEFAULT_APP_QPS):
        await _bump(app_id)
        raise HTTPException(status_code=429, detail="该系统调用量超出配额")
    # 系统工号只受 appId 配额约束：批处理与真人打开用的是两个账号维度，
    # 共用一个 20 QPS 会让"批量导出"把真人预览一起堵住。
    if emp_no and not is_system_emp(emp_no) \
            and not await _allow("e:%s" % emp_no, config.EMP_QPS):
        await _bump(app_id)
        raise HTTPException(status_code=429, detail="当前工号请求过于频繁")
