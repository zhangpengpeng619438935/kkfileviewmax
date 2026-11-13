# -*- coding: utf-8 -*-
"""缓存回收：删掉长期没人看的预览产物，并兜住总量上限。

为什么需要它：
    /app/sheets 里是 PDF、表格快照、页面位图、压缩包本体、3D 网格——都是"可再生"
    的缓存，但没有任何自动回收。`invalidate()` 只清"被再次访问到"的那一个文件，
    真正没人看的会一直占着磁盘。当前 5.9GB / 3813 个文件，按 20 倍流量估算，
    一年内会到几十 GB。

两条规则（先按时间，再按容量）：
    1. 修改时间超过 `--days`（默认 21 天）→ 删。mtime 就是"最后一次生成/续期"的时间，
       `_arc_pkg`、`revalidate` 命中时都会 os.utime，所以它近似等于"最后被使用"。
    2. 删完还超过 `--max-gb`（默认 200GB）→ 按 mtime 从旧到新继续删，直到达标。

安全边界（都实测过，别去掉）：
    - 30 分钟内动过的文件一律不碰：可能正在被写入或刚被续期。
    - `.part`/`.tmp` 临时文件只在超过 1 天后才删（它们可能正被写）。
    - 删文件的同时删掉 Redis 里对应的 `kk:ready:<fk>` 等键，否则接口会"说已就绪、
      但产物不存在"，用户看到 404「PDF 尚未生成」。

用法：
    python3 tools/gc_cache.py --dry-run          # 只看会删什么
    python3 tools/gc_cache.py                    # 真删
    python3 tools/gc_cache.py --days 30 --max-gb 300
"""
import argparse
import os
import re
import subprocess
import sys
import time

DEFAULT_DIR = "/opt/kkfileview-service/data/gateway/sheets"
REDIS_CONTAINER = "kk-redis"
REDIS_DB = "3"
FK_RE = re.compile(r"^([0-9a-f]{64})")


def redis_del(keys):
    """批量删 Redis 键（宿主机没有 redis-cli，走容器）。"""
    if not keys:
        return 0
    lines = []
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        lines.append("DEL " + " ".join(chunk))
    try:
        p = subprocess.run(
            ["docker", "exec", "-i", REDIS_CONTAINER,
             "redis-cli", "-n", REDIS_DB],
            input="\n".join(lines) + "\n", text=True,
            capture_output=True, timeout=300)
        return len([x for x in (p.stdout or "").splitlines() if x.strip() not in ("", "0")])
    except Exception as exc:
        print("Redis 清理失败（产物已删，键会在 TTL 到期后自行消失）: %s" % exc,
              file=sys.stderr)
        return 0


def scan(root):
    items, total = [], 0
    by_fk = {}
    with os.scandir(root) as it:
        for e in it:
            try:
                if not e.is_file(follow_symlinks=False):
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            items.append({"path": e.path, "name": e.name,
                          "mtime": st.st_mtime, "size": st.st_size})
            total += st.st_size
            m = FK_RE.match(e.name)
            if m:
                by_fk.setdefault(m.group(1), []).append(e.path)
    return items, total, by_fk


def _size(n: float) -> str:
    """人类可读：小于 1GB 用 MB，免得"释放 0.00 GB"看不出删了多少。"""
    if n >= 1073741824:
        return "%.2f GB" % (n / 1073741824.0)
    return "%.1f MB" % (n / 1048576.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=DEFAULT_DIR)
    ap.add_argument("--days", type=int, default=21, help="超过这么多天没动就删")
    ap.add_argument("--max-gb", type=float, default=200.0, help="总量上限")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    root = a.dir
    if not os.path.isdir(root):
        print("目录不存在: %s" % root)
        return 2

    now = time.time()
    items, total, by_fk = scan(root)
    print("扫描 %d 个文件，合计 %s" % (len(items), _size(total)))

    keep_recent = 30 * 60                     # 30 分钟内动过的一律不动
    cutoff = now - a.days * 86400
    doomed = []

    for it in items:
        age = now - it["mtime"]
        temp = it["name"].endswith((".part", ".tmp"))
        if temp:
            if age > 86400:                   # 临时文件：1 天后才算残留
                doomed.append(it)
            continue
        if age > keep_recent and it["mtime"] < cutoff:
            doomed.append(it)

    doomed_set = {d["path"] for d in doomed}
    freed = sum(d["size"] for d in doomed)
    remain = total - freed

    # 规则 2：容量兜底
    limit = int(a.max_gb * 1073741824)
    capped = False
    if remain > limit:
        rest = sorted((i for i in items if i["path"] not in doomed_set
                       and now - i["mtime"] > keep_recent),
                      key=lambda x: x["mtime"])
        for it in rest:
            if remain <= limit:
                break
            doomed.append(it)
            doomed_set.add(it["path"])
            freed += it["size"]
            remain -= it["size"]
        capped = True
        if remain > limit:
            print("超过 %.0f GB 上限，但剩余 %s 都是 30 分钟内动过的，本轮不删"
                  % (a.max_gb, _size(remain)))
        else:
            print("超过 %.0f GB 上限，已追加删除至 %s" % (a.max_gb, _size(remain)))

    print("待删 %d 个文件，释放 %s，删后剩余 %s"
          % (len(doomed), _size(freed), _size(remain)))
    if a.dry_run:
        for d in doomed[:10]:
            print("  (dry) %s  %.1fMB  %s"
                  % (d["name"][:44], d["size"] / 1048576.0,
                     time.strftime("%Y-%m-%d", time.localtime(d["mtime"]))))
        print("dry-run，未做任何修改")
        return 0

    keys, gone_fk = [], set()
    ok = 0
    for d in doomed:
        try:
            os.remove(d["path"])
            ok += 1
            m = FK_RE.match(d["name"])
            if m:
                gone_fk.add(m.group(1))
        except OSError as exc:
            print("删除失败 %s: %s" % (d["name"], exc), file=sys.stderr)

    # 只有该 fileKey 的产物全被删了，才清它的就绪标记
    for fk in gone_fk:
        left = [p for p in by_fk.get(fk, []) if os.path.exists(p)]
        if left:
            continue
        for prefix in ("kk:ready:", "kk:failmsg:", "kk:partial:", "kk:srcfp:"):
            keys.append(prefix + fk)
    n = redis_del(keys)
    print("已删 %d 个文件，清理 %d 个 Redis 键（涉及 %d 个文件）"
          % (ok, len(keys), len(gone_fk)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
