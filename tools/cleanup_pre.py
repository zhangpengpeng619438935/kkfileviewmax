#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清掉预转换留下的"本地 fk"冗余产物（一次性 + 可反复跑）。

预转是用本地 URL（`127.0.0.1:8899/_pre/xxx.dwg`）发起转换的，引擎那次转换
在 sheets 和 preview_task 里都留下了一套以**本地 fk** 命名的产物/记录；
真正的缓存是复制到**业务 fk** 的那一套。两套内容一样，等于双倍磁盘
（7.4MB 的布局图每份 PDF 46MB）。

判据：`preview_task.file_url` 指向本地静态服务的就是冗余。

    python3 /opt/kkfileview-service/tools/cleanup_pre.py --dry-run
    python3 /opt/kkfileview-service/tools/cleanup_pre.py
"""
import argparse
import json
import os
import subprocess

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
LOCAL_MARK = "8899/_pre/"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def mysql(sql):
    return sh('docker exec kk-mysql mysql -ukkfileview -pchange-me-db-password kkfileview -N -e '
              + json.dumps(sql)).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    out = mysql("SELECT file_key FROM preview_task WHERE file_url LIKE '%%%s%%'" % LOCAL_MARK)
    fks = [l.strip() for l in out.splitlines() if len(l.strip()) == 64]
    print("本地 fk 记录 %d 条" % len(fks))

    freed = 0
    names = set(os.listdir(SHEETS)) if os.path.isdir(SHEETS) else set()
    hit = 0
    for fk in fks:
        for fn in [n for n in names if n.startswith(fk + ".")]:
            p = os.path.join(SHEETS, fn)
            try:
                size = os.path.getsize(p)
            except OSError:
                continue
            hit += 1
            freed += size
            if not a.dry_run:
                try:
                    os.remove(p)
                except OSError:
                    pass
        if not a.dry_run:
            sh('docker exec kk-redis redis-cli -n 3 del kk:ready:%s kk:srcfp:%s'
               % (fk, fk))
    if not a.dry_run and fks:
        mysql("DELETE FROM preview_task WHERE file_url LIKE '%%%s%%'" % LOCAL_MARK)
    print("产物 %d 个，%s %.1f MB%s"
          % (hit, "将释放" if a.dry_run else "已释放",
             freed / 1048576.0, "（dry-run）" if a.dry_run else ""))


if __name__ == "__main__":
    main()
