# -*- coding: utf-8 -*-
"""把旧 fileKey 的产物认领到新 fileKey —— 改 key 算法后避免全量重转。

背景：2026-11-12 修了 filekey.py 的归一化（路径里的一次性令牌、`?path=` 形态）。
新算法会让一部分 URL 算出不同的键。若不处理，这些文件的缓存全部失配，
下次打开就是一次完整重转 —— 实测 1446 个真实文件受影响，其中 114 个 PPT，
一份 200~333 秒，等于凭空造出几小时的转换负载。

做法：把 `<旧键>.<后缀>` 直接 rename 成 `<新键>.<后缀>`（同目录瞬间完成），
Redis 里的 `kk:ready:<键>` 等一起搬过去，preview_task 的 file_key 一并更新。

合并规则：新键已经有产物时，旧产物直接删掉（同一文件的重复副本）。

用法（容器内）：
    python /app/migrate_keys.py --dry-run     # 先看会动多少
    python /app/migrate_keys.py               # 真做
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app import config, db, engine, filekey, sheet   # noqa: E402

# Redis 里以 fileKey 结尾的键前缀（engine.py 里用到的全部）
KEY_PREFIXES = ("kk:ready:", "kk:srcfp:", "kk:task:", "kk:nosnap:",
                "kk:pdfpref:", "kk:failmsg:", "kk:part:", "kk:url:")

# 不搬的临时文件后缀
SKIP_SUFFIX = (".part", ".tmp")


def old_key_of(url: str) -> str:
    """旧算法：只剔 query 签名，path 原样。"""
    import hashlib
    import urllib.parse
    u = urllib.parse.urlparse(url or "")
    pairs = sorted((k, v) for k, vs in urllib.parse.parse_qs(
        u.query, keep_blank_values=True).items()
        if k.lower() not in filekey.VOLATILE_PARAMS for v in vs)
    base = "%s://%s%s" % (u.scheme.lower(), u.netloc.lower(), u.path)
    q = urllib.parse.urlencode(pairs)
    if q:
        base += "?" + q
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def artifacts_of(fk: str):
    """该键的所有产物文件（按 `<fk>.` 前缀），跳过 .part/.tmp。"""
    out = []
    try:
        with os.scandir(sheet.SHEET_DIR) as it:
            for e in it:
                if not e.name.startswith(fk + "."):
                    continue
                if e.name.endswith(SKIP_SUFFIX):
                    continue
                out.append(e.path)
    except OSError:
        pass
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    await db.init_db()
    # URL 来源要全：audit_log 只记访问过的，preview_task 记的是转换过的，
    # 两边都捞一遍才能覆盖"转了但访问记录被清理"这类情况。
    urls = []
    for sql in ("SELECT DISTINCT file_url FROM audit_log "
                "WHERE file_url LIKE 'http%' AND file_url <> ''",
                "SELECT DISTINCT file_url FROM preview_task "
                "WHERE file_url LIKE 'http%' AND file_url <> ''"):
        try:
            urls.extend(r["file_url"] for r in await db.query(sql) if r["file_url"])
        except Exception as exc:
            print("  查询失败（跳过该来源）: %s" % exc)
    urls = list(dict.fromkeys(urls))

    pairs = {}          # old -> new（同一 old 只算一次）
    for u in urls:
        try:
            o, n = old_key_of(u), filekey.of(u)
        except Exception:
            continue
        if o != n:
            pairs[o] = n

    moved = merged = 0
    moved_bytes = 0
    redis_ops = []
    task_updates = []
    for old, new in pairs.items():
        old_files = artifacts_of(old)
        # 产物可以没有（没转过 / 被 GC 清过），但 Redis 里的指纹、就绪标记如果有，
        # 也必须跟着搬 —— 否则指纹丢失 → revalidate 判不了"源有没有变" → 白重转一次。
        redis_ops.extend((pre, old, new) for pre in KEY_PREFIXES)
        if not old_files:
            continue
        new_has = bool(artifacts_of(new))
        if new_has:
            # 新键已有产物：旧的是重复副本，删掉
            if not args.dry_run:
                for p in old_files:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            merged += 1
            continue
        if args.dry_run:
            moved += 1
            moved_bytes += sum(os.path.getsize(p) for p in old_files)
            continue
        for p in old_files:
            suffix = p[len(os.path.join(sheet.SHEET_DIR, old)):]
            dst = os.path.join(sheet.SHEET_DIR, new + suffix)
            try:
                os.rename(p, dst)
                moved_bytes += os.path.getsize(dst)
            except OSError as exc:
                print("  改名失败 %s -> %s: %s" % (os.path.basename(p), new[:8], exc))
        moved += 1
        task_updates.append((new, old))

    # Redis：只 rename 真的存在的键，避免 8×N 次无效调用
    r = await db.get_redis()
    if redis_ops and not args.dry_run:
        live = set()
        async for k in r.scan_iter(count=500):
            live.add(k)
        for pre, old, new in redis_ops:
            k_old, k_new = pre + old, pre + new
            if k_old not in live or k_new in live:
                continue                    # 旧键没有，或新键已有（以新的为准）
            try:
                await r.rename(k_old, k_new)
            except Exception:
                pass

    # preview_task 里的 file_key 跟着改，否则新键会以为"没任务"
    if task_updates and not args.dry_run:
        for new, old in task_updates:
            try:
                await db.execute(
                    "UPDATE preview_task SET file_key=%s WHERE file_key=%s", (new, old))
            except Exception:
                pass

    print("%s 受影响键 %d 个：搬移 %d、合并重复 %d"
          % ("[dry-run]" if args.dry_run else "[done]", len(pairs), moved, merged))
    print("  搬移体积 %.1f MB" % (moved_bytes / 1048576.0))
    await db.close_db()


asyncio.run(main())
