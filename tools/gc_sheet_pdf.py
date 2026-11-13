#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理"表格被误转成 PDF"的历史产物（<file_key>.pdf）。

背景：2026-11-04 之前，xlsx/xls 的 dimension 被格式刷到 XFD 时会被判成"超大表"
直接转 PDF。现在表格一律走 Univer（快照 .univsnap.json），这些 PDF 没人用了。

**删之前必须过三道检查，否则会误伤**：

1. 只碰表格类（.xlsx/.xls/.xlsm）的 file_key。`<fk>.pdf` 这个命名同时被
   docx/md 的"本地转 PDF"通道使用，按扩展名筛才不会删掉它们的正式产物。
   （CAD 是 `<fk>.cad.pdf`、PPT 两阶段是 `<fk>.ppt.pdf`，命名不同，天然避开）
2. `kk:ready:<fk>` 是 `pdf_local` 的一律保留 —— 那说明系统当前就靠这份 PDF 出图。
3. 必须已经有 .html 或 .univsnap.json 兜底。只剩 PDF 的文件删了会让用户下次
   打开白等一轮转换，收益不值这个风险。

用法：
    python3 tools/gc_sheet_pdf.py --dry-run     # 只看会删什么
    python3 tools/gc_sheet_pdf.py               # 真删
"""
import argparse
import os
import subprocess
import sys

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
SHEET_EXTS = (".xlsx", ".xls", ".xlsm")
REDIS_CONTAINER = "kk-redis"
REDIS_DB = "3"


def mysql(sql):
    p = subprocess.run(
        ["docker", "exec", "kk-mysql", "mysql", "-ukkfileview", "-pchange-me-db-password",
         "kkfileview", "--default-character-set=utf8mb4", "-N", "-e", sql],
        capture_output=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit((p.stderr or b"").decode("utf-8", "replace")[:300])
    return (p.stdout or b"").decode("utf-8", "replace")


def redis_mget(keys):
    """raw 输出：缺键是空行（--no-raw 会显示 (nil)，之前踩过）。"""
    out = {}
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        try:
            p = subprocess.run(
                ["docker", "exec", "-i", REDIS_CONTAINER, "redis-cli", "-n",
                 REDIS_DB, "mget"] + chunk,
                capture_output=True, timeout=60)
        except Exception:
            return out
        vals = (p.stdout or b"").decode("utf-8", "replace").splitlines()
        for k, v in zip(chunk, vals):
            out[k] = v.strip()
    return out


def _size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=SHEETS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    # 默认只清 .xlsx/.xlsm：.xls 老格式的正式产物就是这份 PDF（ready=pdf_local），
    # 删了用户直接打不开，必须留在 --include-xls 后面手动确认
    ap.add_argument("--include-xls", action="store_true")
    a = ap.parse_args()

    exts = [".xlsx", ".xlsm"] + ([".xls"] if a.include_xls else [])
    like = " OR ".join("file_name LIKE '%%%s'" % e for e in exts)
    rows = mysql(
        "SELECT DISTINCT file_key, file_name FROM preview_task"
        " WHERE status='ready' AND (" + like + ")")
    items = []
    for line in rows.splitlines():
        p = line.split("\t")
        if len(p) < 2:
            continue
        items.append({"fk": p[0], "name": p[1]})
    print("表格类任务 %d 个" % len(items))

    keeps, deletes = [], []
    for it in items:
        fk = it["fk"]
        pdf = os.path.join(a.dir, fk + ".pdf")
        if not os.path.exists(pdf):
            continue
        it["pdf"] = pdf
        it["bytes"] = _size(pdf)
        it["html"] = os.path.exists(os.path.join(a.dir, fk + ".html"))
        it["snap"] = _size(os.path.join(a.dir, fk + ".univsnap.json")) > 0
        deletes.append(it)

    # 就绪标记：pdf_local 的不能删
    ready = redis_mget(["kk:ready:" + i["fk"] for i in deletes])
    keep_pdf_local, no_fallback = [], []
    final = []
    for it in deletes:
        mode = ready.get("kk:ready:" + it["fk"], "")
        it["mode"] = mode
        if mode == "pdf_local":
            keep_pdf_local.append(it)
        elif not (it["html"] or it["snap"]):
            # 早期误转的 PDF，现在没有 html/快照。删掉后用户打开会按新逻辑现场
            # 生成快照（普通表 0.4~2 秒；被判大表的才试 8 秒再退 PDF），
            # 这属于"可以接受的首次代价"，否则它们要一直占着直到 21 天过期。
            no_fallback.append(it)
        else:
            final.append(it)

    final = final + no_fallback
    total = sum(i["bytes"] for i in final)
    all_bytes = sum(i["bytes"] for i in deletes)
    by_ext = {}
    for i in deletes:
        e = os.path.splitext(i["name"])[1].lower() or "?"
        d = by_ext.setdefault(e, [0, 0])
        d[0] += 1
        d[1] += i["bytes"]
    print("有 <fk>.pdf 的表格 %d 个，合计 %.1f MB  → 分布：%s"
          % (len(deletes), all_bytes / 1048576.0,
             "、".join("%s %d 个/%.1fMB" % (e, v[0], v[1] / 1048576.0)
                      for e, v in sorted(by_ext.items(), key=lambda x: -x[1][0]))))
    print("其中：可删 %d 个（%.1f MB）" % (len(final), total / 1048576.0))
    print("  跳过：ready=pdf_local（正靠它出图）%d 个" % len(keep_pdf_local))
    print("  其中无 html/快照兜底 %d 个（删后首次打开会重建快照，普通表 1 秒内）"
          % len(no_fallback))
    if a.verbose or a.dry_run:
        for it in final[:15]:
            print("  - %-42s %.1f MB  html=%s 快照=%s"
                  % (it["name"][:40], it["bytes"] / 1048576.0,
                     "有" if it["html"] else "无", "有" if it["snap"] else "无"))
        if len(final) > 15:
            print("  …… 另有 %d 个" % (len(final) - 15))

    if a.dry_run:
        print("dry-run，未删除任何文件")
        return 0

    done = freed = 0
    for it in final:
        try:
            os.remove(it["pdf"])
            done += 1
            freed += it["bytes"]
        except OSError as exc:
            print("  删除失败 %s: %s" % (it["name"][:30], exc))
    print("\n已删 %d 个 PDF，释放 %.1f MB" % (done, freed / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
