#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MySQL 逻辑备份 —— 每天一份，保留 14 天。

为什么需要：access_log / audit_log 是"谁在什么时候看了哪份受控文件"的审计记录，
RETENTION_DAYS=180 意味着要能回溯半年。crontab 里原本**没有任何备份任务**，
MySQL 容器数据卷一坏就全没了，而且没有任何提示。

**为什么不是热备 / binlog**：数据量很小（28MB / 3 万行，一年也就几百 MB），
`--single-transaction` 的 InnoDB 一致性快照足够，成本最低。真要 PITR 再上 binlog。

**为什么导到宿主机而不是容器内**：容器重建会丢 /tmp，备份不能放在被备份的对象里。

用法：
    python3 tools/backup_db.py                # 备份
    python3 tools/backup_db.py --list         # 看现有备份
    python3 tools/backup_db.py --verify XXX   # 校验某份能否正常解开且含关键表
    python3 tools/backup_db.py --days 30      # 改保留期

crontab：
    20 3 * * * cd /opt/kkfileview-service && flock -n /tmp/kk_backup.lock \
      python3 tools/backup_db.py >> /tmp/kk_backup.log 2>&1
"""
import argparse
import glob
import gzip
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKUP_DIR = os.path.join(ROOT, "backup")
CONTAINER = "kk-mysql"
KEEP_DAYS = 14
# 备份里必须出现的表；少一张说明 dump 没导全
MUST_HAVE = ("access_log", "audit_log", "app_account", "preview_task", "alert_log")


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return default


DB = env("MYSQL_DB", "kkfileview")
PWD = env("MYSQL_ROOT_PASSWORD")


def _size(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%.0f KB" % (n / 1024.0)


def _mysqldump_cmd():
    return ["docker", "exec", CONTAINER, "mysqldump", "-uroot", "-p" + PWD,
            "--single-transaction", "--quick", "--default-character-set=utf8mb4",
            "--no-tablespaces", "--routines", "--triggers", DB]


def backup(days=KEEP_DAYS):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(BACKUP_DIR, "kkfileview-%s.sql.gz" % stamp)
    tmp = out + ".part"
    t0 = time.time()
    # 直接管道压缩：28MB 的库 dump 出来约 10MB，先落盘再压没必要
    p = subprocess.Popen(_mysqldump_cmd(), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    written = 0
    try:
        with gzip.open(tmp, "wb", compresslevel=6) as fh:
            while True:
                chunk = p.stdout.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
        err = p.stderr.read().decode("utf-8", "replace")
        p.wait(timeout=60)
    except Exception as exc:
        p.kill()
        try:
            os.remove(tmp)
        except OSError:
            pass
        print("备份失败（中断）: %s" % exc)
        return 1
    if p.returncode != 0 or written == 0:
        msg = [x for x in err.splitlines() if "Warning" not in x]
        print("备份失败: %s" % (msg[0] if msg else "mysqldump 返回 %s" % p.returncode))
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 1
    os.replace(tmp, out)
    size = os.path.getsize(out)
    # 立刻验证一次：能解开、含关键表。备份文件坏了比没有更危险
    ok, why = verify(out)
    print("%s %s  %.1f MB（原始 %.1f MB）  %.1fs  %s"
          % (time.strftime("%F %T"), os.path.basename(out), size / 1048576.0,
             written / 1048576.0, time.time() - t0,
             "校验通过" if ok else ("校验失败：" + why)))
    if not ok:
        return 2
    purge(days)
    return 0


def verify(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            head = fh.read(400000)
    except Exception as exc:
        return False, str(exc)[:80]
    if "CREATE TABLE" not in head and "MySQL dump" not in head:
        return False, "看起来不是 mysqldump 输出"
    missing = [t for t in MUST_HAVE if ("`%s`" % t) not in head]
    if missing:
        # 关键表可能排在 400KB 之后，扫全文确认一次
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
                rest = fh.read()
            missing = [t for t in MUST_HAVE if ("`%s`" % t) not in rest]
        except Exception as exc:
            return False, str(exc)[:80]
    if missing:
        return False, "缺少表 " + ", ".join(missing)
    return True, ""


def purge(days):
    cut = time.time() - days * 86400
    kept = removed = 0
    for p in sorted(glob.glob(os.path.join(BACKUP_DIR, "kkfileview-*.sql.gz"))):
        if os.path.getmtime(p) < cut:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
        else:
            kept += 1
    if removed:
        print("  清理 %d 份超过 %d 天的备份，保留 %d 份" % (removed, days, kept))


def listing():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "kkfileview-*.sql.gz")))
    if not files:
        print("(还没有备份)")
        return
    total = sum(os.path.getsize(p) for p in files)
    print("%-26s %10s  %s" % ("文件", "大小", "时间"))
    for p in files:
        print("%-26s %10s  %s" % (os.path.basename(p), _size(os.path.getsize(p)),
                                  time.strftime("%F %T", time.localtime(os.path.getmtime(p)))))
    print("共 %d 份，%.1f MB" % (len(files), total / 1048576.0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=KEEP_DAYS)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", metavar="FILE")
    args = ap.parse_args()
    if args.list:
        listing()
        return 0
    if args.verify:
        ok, why = verify(args.verify)
        print("校验 %s: %s" % (args.verify, "通过" if ok else "失败 - " + why))
        return 0 if ok else 2
    return backup(args.days)


if __name__ == "__main__":
    sys.exit(main())
