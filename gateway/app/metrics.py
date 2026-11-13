# -*- coding: utf-8 -*-
"""运行指标：给 Prometheus 抓的极小实现。

为什么自己写而不是上 prometheus_client：
    只有两个 worker、四五个指标，多一个依赖不值得。这里只维护
    进程内的计数器 + 最近 2000 次耗时，按 Prometheus 文本格式输出；
    跨进程聚合交给 Prometheus（两个 worker 用 pid 区分，计数求和、
    延迟取各自的 quantile）。

注意：
    - 计数是**本进程**的，重启清零。需要长期趋势请看 MySQL 的 access_log。
    - 延迟分位数取自最近 2000 次请求的滑动样本，不代表全天。
"""
import collections
import os
import shutil
import threading
import time

_lock = threading.Lock()
_counts = collections.Counter()          # (group, status) -> 次数
_latency = collections.deque(maxlen=2000)  # 最近 2000 次耗时（毫秒）
_started = time.time()
_cache_bytes = {"at": 0.0, "value": 0}


def group_of(path: str) -> str:
    """把路径归成有限的几组，避免每个 fileKey 都变成一条时间序列。"""
    if path.startswith("/api/v1/preview/"):
        return "/api/v1/preview/*"
    if path.startswith("/api/v1/univer/"):
        return "/api/v1/univer/*"
    if path.startswith("/api/v1/auth/"):
        return "/api/v1/auth/*"
    if path.startswith("/api/v1/admin/"):
        return "/api/v1/admin/*"
    if path.startswith("/api/v1/stats"):
        return "/api/v1/stats*"
    if path.startswith("/pdf/"):
        return "/pdf/*"
    if path.startswith("/univer/"):
        return "/univer/*"
    if path.startswith("/pdfview/"):
        return "/pdfview/*"
    if path.startswith("/stepview/") or path.startswith("/stepdata/"):
        return "/stepview/*"
    if path.startswith("/archive/"):
        return "/archive/*"
    if path.startswith("/kk/"):
        return "/kk/*"
    if path.startswith("/sheet/"):
        return "/sheet/*"
    if path in ("/health", "/metrics"):
        return path
    return "other"


def observe(group: str, status: int, ms: int):
    with _lock:
        _counts[(group, int(status))] += 1
        _latency.append(max(0, int(ms)))


def _quantile(sorted_ms, q):
    if not sorted_ms:
        return 0.0
    idx = min(len(sorted_ms) - 1, int(len(sorted_ms) * q))
    return float(sorted_ms[idx])


def cache_dir_bytes(path: str, ttl: float = 30.0) -> int:
    """缓存目录占用。目录里可能上千个文件，30 秒内复用上次结果。"""
    now = time.time()
    if _cache_bytes["at"] and now - _cache_bytes["at"] < ttl:
        return int(_cache_bytes["value"])
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    except OSError:
        total = 0
    _cache_bytes.update({"at": now, "value": total})
    return total


def render(extra: dict | None = None) -> str:
    """Prometheus 文本格式。extra 里放需要现查的（转换队列、磁盘等）。"""
    with _lock:
        counts = dict(_counts)
        lat = sorted(_latency)
    lines = []

    def gauge(name, help_text, value, labels=""):
        lines.append("# HELP %s %s" % (name, help_text))
        lines.append("# TYPE %s gauge" % name)
        lines.append("%s%s %s" % (name, labels, value))

    lines.append("# HELP kk_requests_total 按路径分组与状态码统计的请求数")
    lines.append("# TYPE kk_requests_total counter")
    for (group, status), n in sorted(counts.items()):
        lines.append('kk_requests_total{group="%s",status="%d"} %d'
                     % (group, status, n))

    n429 = sum(v for (g, s), v in counts.items() if s == 429)
    lines.append("# HELP kk_ratelimited_total 被限流拦截的请求数")
    lines.append("# TYPE kk_ratelimited_total counter")
    lines.append("kk_ratelimited_total %d" % n429)

    # quantile 的 label 值用数字（0.95），这是 Prometheus summary 的约定；
    # 同一个指标只写一次 # TYPE，重复写虽然能解析，但导出器会报格式告警。
    lines.append("# HELP kk_latency_ms 本进程最近 2000 次请求的耗时分位")
    lines.append("# TYPE kk_latency_ms gauge")
    for q in (0.5, 0.95, 0.99):
        lines.append('kk_latency_ms{quantile="%s"} %.1f' % (q, _quantile(lat, q)))
    lines.append("# TYPE kk_latency_samples gauge")
    lines.append("kk_latency_samples %d" % len(lat))

    gauge("kk_uptime_seconds", "本进程运行时长", "%.0f" % (time.time() - _started))
    helps = {
        "converting": "正在转换的文件数",
        "queued": "排队等待转换的文件数",
        "cache_bytes": "预览缓存目录占用字节",
        "disk_free_bytes": "缓存所在磁盘剩余字节",
        "workers": "网关进程数",
    }
    for k, v in (extra or {}).items():
        if isinstance(v, (int, float)):
            gauge("kk_" + k, helps.get(k, k), v)
    return "\n".join(lines) + "\n"


def disk_free_bytes(path: str) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return 0
