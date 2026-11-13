# -*- coding: utf-8 -*-
"""验证 prewarm_hot.stale()：对指定文件判断"标了就绪但源已变"。"""
import os
import sys

sys.path.insert(0, "/opt/kkfileview-service/tools")
import prewarm_hot as pw          # noqa: E402
import preconvert as pc           # noqa: E402

names = ["Z CAM GY11 IC015A 03  洛柯DVR控制计划（V1.4） .xls",
         "Z HUD GY11 W013A 01_JLR_L384_DFMEA_V1.1.xlsx",
         "AR034A样品承认.xlsx"]
for n in names:
    sql = ("SELECT file_key, MAX(file_url) FROM preview_task WHERE file_name='%s'"
           " GROUP BY file_key ORDER BY MAX(ready_at) DESC LIMIT 1"
           % n.replace("'", "''"))
    row = pw.mysql(sql).split("\t")
    if len(row) < 2:
        print("查不到: %s" % n)
        continue
    fk, url = row[0], row[1]
    saved = pw.src_fps(["kk:srcfp:" + fk])
    st = pw.stale(fk, url, saved)
    print("%-52s fk=%s  源已变=%s" % (n[:50], fk[:12], "是 → 会重新预热" if st else "否 → 跳过"))
    if st:
        print("     保存 %s" % saved.get("kk:srcfp:" + fk, "")[:60])
        print("     当前 %s" % pw.local_fp(url))
