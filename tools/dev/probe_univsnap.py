# -*- coding: utf-8 -*-
"""验证：走一次 _render_xlsx_html，Univer 快照是否顺带生成、耗时多少。

容器内跑：
    docker cp tools/dev/probe_univsnap.py kk-gateway:/tmp/p.py
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py 3
"""
import asyncio
import os
import sys
import time

from app import db, engine, sheet, univer_data


async def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    await db.init_db()
    rows = await db.query(
        "SELECT DISTINCT file_key, file_url, file_name FROM preview_task"
        " WHERE status='ready' AND file_name LIKE '%%.xlsx' LIMIT 300")
    todo = []
    for r in rows:
        snap = os.path.join(sheet.SHEET_DIR, r["file_key"] + ".univsnap.json")
        if not os.path.exists(snap):
            todo.append(r)
    print("候选（无快照的 xlsx）%d 个，取前 %d 个试" % (len(todo), limit))
    for r in todo[:limit]:
        fk, url, name = r["file_key"], r["file_url"], r["file_name"]
        snap = os.path.join(sheet.SHEET_DIR, fk + ".univsnap.json")
        html = sheet.sheet_path(fk)
        # 先看它为什么被判"大"：dimension 虚高（格式刷到 XFD）还是真有内容
        try:
            client = await engine.http()
            fr = await client.get(url, timeout=60)
            why = univer_data.heavy_reason(fr.content) if fr.status_code < 400 else \
                "下载失败 HTTP %s" % fr.status_code
            print("  %-40s heavy=%s" % (name[:38], why or "否"))
        except Exception as exc:
            print("  %-40s 取源失败: %s" % (name[:38], str(exc)[:60]))
        t0 = time.time()
        try:
            as_pdf = await engine._render_xlsx_html(url, fk, name)
            cost = time.time() - t0
            size = os.path.getsize(snap) if os.path.exists(snap) else 0
            print("  %-40s 转PDF=%s 耗时 %.1fs 快照 %s"
                  % (name[:38], as_pdf, cost,
                     ("%.0f KB" % (size / 1024.0)) if size else "未生成"))
            print("      html %s" % (("%.0f KB" % (os.path.getsize(html) / 1024.0))
                                     if os.path.exists(html) else "无"))
        except Exception as exc:
            print("  %-40s 失败: %s" % (name[:38], str(exc)[:80]))
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
