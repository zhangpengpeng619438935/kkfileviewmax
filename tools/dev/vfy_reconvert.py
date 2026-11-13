# -*- coding: utf-8 -*-
"""端到端验证：源文件已变 → 打开时会不会自动重转并换成新内容。

步骤：记录源指纹/产物 mtime → 调 preview/url（模拟用户打开）→ 等后台转完
→ 再取源指纹/产物 mtime，看有没有被刷新。
"""
import os
import sys
import time

sys.path.insert(0, "/opt/kkfileview-service/tools")
import prewarm_hot as pw          # noqa: E402
import preconvert as pc           # noqa: E402

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
NAME = sys.argv[1] if len(sys.argv) > 1 else "Z CAM GY11 IC015A 03  洛柯DVR控制计划（V1.4） .xls"


def arts(fk):
    out = []
    for n in os.listdir(SHEETS):
        if n.startswith(fk + "."):
            p = os.path.join(SHEETS, n)
            out.append((n, os.path.getsize(p), int(os.path.getmtime(p))))
    return out


sql = ("SELECT file_key, MAX(file_url) FROM preview_task WHERE file_name='%s'"
       " GROUP BY file_key ORDER BY MAX(ready_at) DESC LIMIT 1" % NAME.replace("'", "''"))
fk, url = pw.mysql(sql).split("\t")[:2]

before_fp = pw.src_fps(["kk:srcfp:" + fk]).get("kk:srcfp:" + fk, "")
before = arts(fk)
print("文件 %s\nfk=%s" % (NAME, fk))
print("源指纹(旧) %s" % before_fp[:60])
print("产物(旧) %s" % [(a[0][len(fk) + 1:], a[1]) for a in before])
print("本地源指纹  %s" % pw.local_fp(url))

tk = pc.http_json("/api/v1/auth/token",
                  {"appId": "kk-demo", "appSecret": "your-app-secret"}
                  )["data"]["accessToken"]
t0 = time.time()
r = pc.http_json("/api/v1/preview/url",
                 {"fileUrl": url, "empNo": "SYS_PREWARM", "waitSeconds": 90}, tk,
                 timeout=180)
d = r.get("data") or {}
print("打开结果 status=%s 用时 %.1fs" % (d.get("status"), time.time() - t0))

time.sleep(2)
after_fp = pw.src_fps(["kk:srcfp:" + fk]).get("kk:srcfp:" + fk, "")
after = arts(fk)
print("源指纹(新) %s" % after_fp[:60])
print("产物(新) %s" % [(a[0][len(fk) + 1:], a[1], int(time.time()) - a[2]) for a in after])
print("\n结论：%s" % ("已重转，缓存换成新内容" if after != before else "产物未变化（可能仍命中旧缓存）"))
