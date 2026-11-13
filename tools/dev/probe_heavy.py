# -*- coding: utf-8 -*-
"""实测"dimension 到 XFD"的表格：生成 Univer 快照到底要多久。

决定 heavy 判定要不要直接转 PDF，还是可以试一把快照。
"""
import asyncio
import sys
import time

from app import db, engine, univer_data


async def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "Z HUD BOM AR006C"
    await db.init_db()
    row = await db.query_one(
        "SELECT file_key, file_url, file_name FROM preview_task"
        " WHERE file_name LIKE %s LIMIT 1", (kw + "%",))
    if not row:
        print("查不到任务: %s" % kw)
        return
    print("文件: %s" % row["file_name"])
    print("heavy: %s" % (univer_data.heavy_reason(
        (await (await engine.http()).get(row["file_url"], timeout=60)).content) or "否"))
    client = await engine.http()
    fr = await client.get(row["file_url"], timeout=120)
    print("源文件 %.1f MB" % (len(fr.content) / 1048576.0))
    t0 = time.time()
    try:
        wb = univer_data.xlsx_to_workbook(fr.content, row["file_name"])
        cost = time.time() - t0
        sheets = wb.get("sheets") or {}
        cells = 0
        for sid, sh in sheets.items():
            for rowid in (sh.get("cellData") or {}):
                cells += len(sh["cellData"][rowid] or {})
        import json
        size = len(json.dumps(wb, ensure_ascii=False))
        print("快照生成 %.1fs，工作表 %d 个，非空单元格 %d 个，JSON %.1f MB"
              % (cost, len(sheets), cells, size / 1048576.0))
    except Exception as exc:
        print("快照生成失败 %.1fs: %s" % (time.time() - t0, str(exc)[:100]))
    await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
