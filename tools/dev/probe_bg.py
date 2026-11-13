# -*- coding: utf-8 -*-
"""验证"页面切后台"的样本不打 e2e 标记（不进加载耗时统计、不触发 load_timeout）。

容器内跑：
    docker cp tools/dev/probe_bg.py kk-gateway:/tmp/p.py
    docker exec -w /app -e PYTHONPATH=/app kk-gateway python3 /tmp/p.py
"""
import asyncio
import uuid

from app import db, loadmetric


async def case(tag, bg):
    rid = "bgprobe" + uuid.uuid4().hex[:8]
    await db.execute(
        "INSERT INTO access_log (request_id, app_id, emp_no, file_key, action,"
        " cost_ms, e2e, status_code, success) VALUES (%s,'probe','PROBE','x',"
        "'preview',10,0,200,1)", (rid,))
    await loadmetric.mark_request(rid, file_key="x")
    res = await loadmetric.report(rid, "visible", bg=bg)
    row = await db.query_one(
        "SELECT cost_ms, e2e FROM access_log WHERE request_id=%s", (rid,))
    await db.execute("DELETE FROM access_log WHERE request_id=%s", (rid,))
    print("%-10s report=%s" % (tag, {k: res.get(k) for k in
                                     ("ok", "background", "elapsedMs")}))
    print("%-10s 落库 cost_ms=%s e2e=%s  → %s"
          % ("", row["cost_ms"], row["e2e"],
             "计入统计" if row["e2e"] == 1 else "不计入统计"))


async def main():
    await db.init_db()
    try:
        await case("前台样本", False)
        await case("切后台", True)
    finally:
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
