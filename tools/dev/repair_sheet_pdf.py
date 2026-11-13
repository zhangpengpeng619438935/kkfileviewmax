# -*- coding: utf-8 -*-
"""给表格补 Univer 快照，然后删掉已经没用的 <file_key>.pdf。

为什么要"先补后删"：
    早期 xlsx 的 dimension 被格式刷到 XFD 时会被判成超大表直接转 PDF，
    这些文件既没有 .html 也没有 .univsnap.json，PDF 是唯一的产物。
    直接删掉它们 = 用户下次打开要现场重转（最坏 20 秒）。
    先按新逻辑生成快照，快照成功后才删 PDF，用户下次打开直接读 JSON。

不碰的两类（脚本里硬性跳过）：
    kk:ready:<fk> == pdf_local   —— .xls 老格式的正式产物就是这份 PDF，删了打不开
    快照生成失败                  —— 源文件 403/404（业务接口要会话），保持原样

容器内跑（要 import app、要用 engine 的下载与解析）：
    docker cp tools/dev/repair_sheet_pdf.py kk-gateway:/tmp/p.py
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py --dry-run
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py
"""
import asyncio
import os
import sys

from app import db, engine, sheet


async def main():
    dry = "--dry-run" in sys.argv
    await db.init_db()
    rows = await db.query(
        "SELECT DISTINCT file_key, file_url, file_name FROM preview_task"
        " WHERE status='ready' AND (file_name LIKE '%%.xlsx'"
        "   OR file_name LIKE '%%.xls' OR file_name LIKE '%%.xlsm')")
    print("表格类任务 %d 个" % len(rows))

    cand = []
    for r in rows:
        pdf = sheet.local_pdf_path(r["file_key"])
        if not os.path.exists(pdf):
            continue
        mode = await engine.ready_mode(r["file_key"])
        if mode == "pdf_local":
            continue                     # .xls 的正式产物，留着
        cand.append({"fk": r["file_key"], "url": r["file_url"],
                     "name": r["file_name"], "pdf": pdf,
                     "bytes": os.path.getsize(pdf),
                     "snap": sheet.snap_ready(r["file_key"]),
                     "html": sheet.sheet_ready(r["file_key"])})
    print("可处理（有 PDF 且 ready 不是 pdf_local）%d 个，合计 %.1f MB"
          % (len(cand), sum(c["bytes"] for c in cand) / 1048576.0))

    # 源文件已经判过"不可达"（403/404）的，补快照必然失败，别去等下载超时
    redis = await db.get_redis()
    built = skipped = 0
    for c in cand:
        if c["snap"] or c["html"]:
            continue
        try:
            if await redis.get("kk:pre:fail:" + c["fk"]):
                skipped += 1
                continue
        except Exception:
            pass
        # 没有兜底产物：按新逻辑生成快照
        try:
            client = await engine.http()
            fr = await client.get(c["url"], timeout=30)
            if fr.status_code >= 400:
                print("  取源失败 %-32s HTTP %s" % (c["name"][:30], fr.status_code))
                continue
            ok = await engine._build_univsnap(fr.content, c["name"], c["fk"], timeout=60)
            c["snap"] = ok
            if ok:
                built += 1
                print("  快照已补 %-32s %.1f MB → %s"
                      % (c["name"][:30], c["bytes"] / 1048576.0,
                         ("%.0f KB" % (os.path.getsize(sheet.snap_path(c["fk"])) / 1024.0))))
        except Exception as exc:
            print("  补快照失败 %-30s %s" % (c["name"][:28], str(exc)[:60]))

    ready_to_del = [c for c in cand if c["snap"] or c["html"]]
    keep = [c for c in cand if not (c["snap"] or c["html"])]
    freed = sum(c["bytes"] for c in ready_to_del)
    print("\n补快照 %d 个（源不可达跳过 %d 个）；可删 PDF %d 个（%.1f MB）；"
          "仍无兜底保留 %d 个"
          % (built, skipped, len(ready_to_del), freed / 1048576.0, len(keep)))
    if dry:
        print("dry-run，未删除任何文件")
    else:
        done = 0
        for c in ready_to_del:
            try:
                os.remove(c["pdf"])
                done += 1
            except OSError as exc:
                print("  删除失败 %s: %s" % (c["name"][:26], exc))
        print("已删 %d 个 PDF，释放 %.1f MB" % (done, freed / 1048576.0))


if __name__ == "__main__":
    asyncio.run(main())
