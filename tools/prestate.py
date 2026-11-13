#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预转换的失败状态与冷却（preconvert / preconvert_step 共用）。

**为什么需要**

crontab 每小时跑一轮 `preconvert.py`（DWG）与 `preconvert_step.py`（STEP），
失败的文件没有任何记忆，于是被无限重试。实测线上：

| 脚本 | 现象 |
|---|---|
| `pre_dwg.log` | 52 个文件 **每轮全失败**，272 轮 5872 次，其中 49 个是不同文件，接口 400「文件地址不可访问(HTTP 404)」——**2026-11-11 查明真因是 preconvert 的本地 URL 少了 .pid 后缀，不是源文件被删；已修** |
| `pre_step.log` | 38 个空网格（gz 仅 115 字节）每轮重解析，**单轮 110 分钟**，因为它写出的 gz < 1KB 不满足"已是最新"的判定 |

**策略**：按"错误类型 + 连续失败次数"做指数退避，命中冷却就静默跳过
（不下载、不调接口、不写明细日志）。源文件指纹变了立刻解除冷却 ——
文件被换成能用的版本应该马上重试，而不是等到期。

    notfound  源文件不存在/不可访问（404、400「文件地址不可访问」）  7d → 30d → 90d
    empty     解析出来没有几何（空装配、只有曲面线框、导出为空）      1d → 7d → 90d
    parse     解析报错（格式怪、模型损坏）                            1d → 3d → 30d
    temp      超时 / 连接失败 / 5xx，可能自愈                          1h → 6h → 1d

**notfound 为什么首次就 7 天**：源文件被删/被新版替换是**确定性事件**，不会过一夜
自己回来。原来首档 1 天，等于每天都要再撞一次同一堵墙 —— 实测 2026-11-12 早高峰
预热里 4 个 PDF 已整版升级（V0.1→V1.1）、旧地址永久 404，第二天还会再报一遍。
真正会自愈的是 temp（连接抖动、源站限流），那一档仍是 1 小时。

反过来，如果"一下子冒出一大批 404"，先怀疑自己的地址拼错（2026-11-11 的
preconvert 就是：本地静态 URL 少了 .pid，193 轮全是 404）。判据很简单 ——
日志汇总行里「完成 N」长期恒为 0，就不是个别文件坏了，是链路断了。

**用法**

    python3 tools/prestate.py --list              # 看当前哪些文件处于冷却
    python3 tools/prestate.py --forget <fileKey>  # 强制下次重试单个文件
    python3 tools/prestate.py --reset             # 清空全部（慎用，会恢复每小时重试）
"""
import argparse
import base64
import json
import subprocess
import sys
import time

KEY = "kk:pre:fail:%s"

# 连续失败第 n 次对应的冷却秒数（n 超过表长就取最后一项）
COOL = {
    # 源文件被删/被新版替换是确定性事件，不用留 1 天的"观察期"；源文件真回来了
    # 会被指纹变化检测到并立刻解除（见 blocked()），所以首档拉长不会误伤
    "notfound": (7 * 86400, 30 * 86400, 90 * 86400),
    "empty": (86400, 7 * 86400, 90 * 86400),
    "parse": (86400, 3 * 86400, 30 * 86400),
    "temp": (3600, 6 * 3600, 86400),
}
KIND_CN = {
    "notfound": "源文件不存在",
    "empty": "没有可解析的实体",
    "parse": "解析失败",
    "temp": "临时错误",
}


def _sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def _rcli(*args):
    return _sh("docker exec kk-redis redis-cli -n 3 " + " ".join(args))


def decode(raw):
    """解析一条冷却记录（base64(JSON)）。解不出来当作没有。"""
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw.encode()).decode("utf-8"))
    except (ValueError, TypeError):
        return None


def load_many(fks):
    """批量读冷却状态 {fk: state}。

    为什么不是循环 _load：热点预热一轮要判 500 个文件，逐个 gets 就是 500 次
    docker exec（每次几十毫秒），光查冷却就要半分钟。这里用 mget 一次拿 200 个。
    键名是 64 位 hex，直接当参数传，不经过 shell，没有注入与引号问题。
    """
    if not fks:
        return {}
    out = {}
    for i in range(0, len(fks), 200):
        chunk = fks[i:i + 200]
        try:
            p = subprocess.run(
                ["docker", "exec", "-i", "kk-redis", "redis-cli", "-n", "3",
                 "mget"] + [KEY % k for k in chunk],
                capture_output=True, timeout=60)
        except Exception:
            return out
        vals = (p.stdout or b"").decode("utf-8", "replace").splitlines()
        for k, v in zip(chunk, vals):
            st = decode(v.strip())
            if st:
                out[k] = st
    return out


def _load(fk):
    """值用 base64 包一层：错误信息里带引号和中文，直接拼 shell 会炸。"""
    raw = _rcli("get", KEY % fk).stdout.strip()
    return decode(raw)


def _save(fk, st, ttl):
    v = base64.b64encode(json.dumps(st, ensure_ascii=False).encode("utf-8")).decode()
    _rcli("set", KEY % fk, v, "EX", str(max(int(ttl), 60)))


def _drop(fk):
    _rcli("del", KEY % fk)


def cool_seconds(kind, n):
    tbl = COOL.get(kind) or COOL["parse"]
    return tbl[min(max(int(n), 1), len(tbl)) - 1]


def kind_of(err, status=None):
    """把各种报错归成 4 类。分不清就算 parse（保守：冷却 1 天起）。"""
    t = str(err or "").lower()
    if (status == 404 or "404" in t or "不可访问" in t or "not found" in t
            or "no such file" in t):
        return "notfound"
    # 429 是对方限流，不是文件坏了：预热门 3 并发拉 60 个文件时很容易撞上
    # （实测 127.0.0.1:8090 一次预热就打回 20 多个 429）。以前这类落到 parse，
    # 冷却 1~30 天，等于一天内再也不预热了；按 temp 处理，1 小时后自动重试。
    if ("429" in t or "超出配额" in t or "too many requests" in t
            or "rate limit" in t or "限流" in t):
        return "temp"
    if ("timeout" in t or "timed out" in t or "超时" in t or "connection" in t
            or " 500" in t or " 502" in t or " 503" in t or "temporarily" in t):
        return "temp"
    if ("no mesh" in t or "没有网格" in t or "空网格" in t or "网格为空" in t
            or "doesn't contain" in t or "empty" in t):
        return "empty"
    return "parse"


def blocked(fk, fp=""):
    """空串 = 可以处理；否则返回一句给日志看的说明（调用方只在首次打印）。"""
    st = _load(fk)
    if not st:
        return ""
    if fp and st.get("fp") and st["fp"] != fp:
        _drop(fk)                       # 源文件换过了，立刻重试
        return ""
    until = st.get("until", 0)
    if time.time() >= until:
        return ""
    return "%s×%d，冷却到 %s" % (KIND_CN.get(st.get("kind"), st.get("kind")),
                              int(st.get("n", 0)),
                              time.strftime("%m-%d %H:%M", time.localtime(until)))


def note_fail(fk, kind, err, fp=""):
    """记一次失败。返回 (状态, 是否值得打明细)。

    只在"第一次失败"与"冷却期升级"时返回 True —— 其余轮次静默跳过，
    这样日志里每个坏文件最多两三条，而不是每轮一条。
    """
    now = time.time()
    st = _load(fk) or {"n": 0, "first": now}
    prev_gap = max(st.get("until", 0) - now, 0)
    st["n"] = int(st.get("n", 0)) + 1
    st["last"] = now
    st["kind"] = kind
    st["err"] = str(err or "")[:180]
    gap = cool_seconds(kind, st["n"])
    st["until"] = now + gap
    if fp:
        st["fp"] = fp
    # 存到"冷却结束 + 30 天"：冻结记录不会永久占着 Redis
    _save(fk, st, gap + 30 * 86400)
    return st, bool(st["n"] == 1 or gap > prev_gap * 2)


def note_ok(fk):
    _drop(fk)


def items():
    """所有处于失败状态的文件：[(fk, state)]。"""
    out = _rcli("--scan", "--pattern", "'kk:pre:fail:*'").stdout.split()
    res = []
    for k in out:
        fk = k.rsplit(":", 1)[-1]
        st = _load(fk)
        if st:
            res.append((fk, st))
    return res


def main():
    ap = argparse.ArgumentParser(description="预转换失败状态与冷却")
    ap.add_argument("--list", action="store_true", help="列出失败状态与冷却期")
    ap.add_argument("--forget", metavar="FILE_KEY", help="清掉单个文件的失败状态")
    ap.add_argument("--reset", action="store_true", help="清空全部失败状态（会恢复每小时重试）")
    a = ap.parse_args()

    if a.forget:
        _drop(a.forget)
        print("已清除", a.forget)
        return 0
    if a.reset:
        n = 0
        for fk, _ in items():
            _drop(fk)
            n += 1
        print("已清空 %d 条失败状态" % n)
        return 0

    rows = items()
    if not rows:
        print("没有处于失败状态的文件")
        return 0
    now = time.time()
    cnt = {}
    frozen = 0
    for fk, st in rows:
        cnt[st.get("kind", "?")] = cnt.get(st.get("kind", "?"), 0) + 1
        if st.get("until", 0) - now > 30 * 86400:
            frozen += 1
    print("共 %d 个文件处于失败状态，其中长期冻结（>30 天）%d 个" % (len(rows), frozen))
    for k, v in sorted(cnt.items(), key=lambda x: -x[1]):
        print("  %-9s %s  %d 个" % (k, KIND_CN.get(k, ""), v))
    if a.list:
        print()
        for fk, st in sorted(rows, key=lambda x: -x[1].get("last", 0)):
            print("%s  n=%-3d %-8s 到 %s  %s"
                  % (fk[:12], st.get("n", 0), KIND_CN.get(st.get("kind"), "?"),
                     time.strftime("%m-%d %H:%M", time.localtime(st.get("until", 0))),
                     str(st.get("err", ""))[:70]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
