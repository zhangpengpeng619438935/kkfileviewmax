#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""热点预热：把"最近被频繁打开"的文件在早高峰前转好（只挑需要转码的）。

为什么只挑热点：
    全量预转不现实（受控文件上万，LibreOffice 并发有限，转一遍要几小时），
    而访问是长尾的——按 access_log 统计，前 500 个文件覆盖了大部分打开次数。
    把这些文件在 6:30 前转好，早高峰的冷转换基本只剩"当天新增/改动"的文件。

"热点"的判定：最近 10 天里 **成功打开过** 的次数（access_log.success=1）。
**直出型文件不参与**（见 DIRECT_EXTS）：网关对 PDF/图片/音视频/纯文本是直接
内联返回，不经过引擎，预热它们既没有收益，又会制造噪音 —— audit_log 存的是
当时的业务地址，受控文件被新版替换后那个地址永久失效，而它在访问记录里还是
"热点"，于是每天预热都拿旧地址去撞一次 404。实测 TOP 500 里约一半（230 个）
是直出型，把它们排掉等于把名额全留给真正需要转码的文件。

源地址从 audit_log 取（它带 file_url，且覆盖所有类型）。**不能用 preview_task**：
只有"需要转换"的文件才会有任务行，PDF/JPG/TXT 这类直出文件根本没有；
实测 10 天里成功打开的 1532 个文件中有 954 个（62%）没有任务行。

和 preconvert.py 的区别：
    preconvert 直接读 Ceph 本地文件、自己算 fileKey（绕过 5 分钟过期的业务签名），
    适用于"全量、按扩展名"的离线预转；
    本脚本走线上接口，用真实业务 URL 触发转换，所以不用管签名规则，
    但要求 URL 仍然有效（这也是它只能挑"最近 7 天被打开过"的文件的原因）。

用法（127.0.0.1 宿主机）：
    python3 tools/prewarm_hot.py --dry-run
    python3 tools/prewarm_hot.py --top 300 --workers 3
    python3 tools/prewarm_hot.py --days 14 --top 500
"""
import argparse
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import formatdate
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preconvert as pc  # noqa: E402
import prestate  # noqa: E402

# 系统工号：不受"单工号 20 QPS"限制（见 gateway/app/ratelimit.py），
# 但也别把 appId 的 60 QPS 打满——3 个并发足够了。
EMP = "SYS_PREWARM"

# 宿主机上源文件的位置（容器内是 /cephfs，宿主机挂的是 /cephfs_data）。
CEPH_FILES = "/cephfs_data/fileManagement/files"
CEPH_ROOT = pc.ROOT          # 与网关 CEPH_ROOT 一致：受控文件根

# "直出型"：网关不经过转码引擎、直接把源字节内联返回的扩展名。
# 预热对它们零收益（首次打开本来就在百毫秒级），却会在文件换版后制造 404
# —— 见文件头说明。口径与 gateway/app/archive_browse.py 的
# IMG_EXT / PDF_EXT / AV_EXT / _TEXT_EXT 对齐，那边增删时记得同步这里。
# 注意 SHEET/DOC/SLIDE 不在此列：xlsx/docx/pptx 要过 LibreOffice，预热有收益。
DIRECT_EXTS = (
    # 图片
    "jpg jpeg png gif bmp webp svg ico avif jfif tif tiff dng"
    # PDF
    " pdf"
    # 音视频
    " mp4 webm ogv mp3 wav m4a aac oga flac"
    # 纯文本 / 源码 / 结构化文本
    " txt log csv tsv md markdown json xml yaml yml ini cfg conf properties toml"
    " sql py js mjs cjs ts tsx jsx java kt c h cpp hpp cc cs go rs rb php sh bash"
    " bat cmd ps1 html htm css scss less vue svelte lua pl r m matlab vb vbs asm"
    " srt ass vtt diff patch gitignore env lst map"
).split()


def local_fp(url):
    """宿主机直接 stat 源文件，拿到与网关 local_fingerprint 同格式的 "大小|mtime"。

    业务地址两种形态：
      http://127.0.0.1:8090/files/<相对 fileManagement/files 的路径>   ← audit_log 里绝大多数
      http://127.0.0.1:8080/api/file/raw?path=/<相对受控文件根的路径>   ← 预转脚本自己拼的
    拿不到（路径不存在/不是这两种）返回 ""，调用方按"判断不了"处理，绝不当成"变了"。
    """
    try:
        u = urlparse(url)
        rel = (parse_qs(u.query).get("path") or [""])[0]
        if rel:
            path = os.path.join(CEPH_ROOT, rel.lstrip("/"))
        else:
            p = unquote(u.path)
            if not p.startswith("/files/"):
                return ""
            path = os.path.join(CEPH_FILES, p[len("/files/"):])
        st = os.stat(path)
    except (OSError, ValueError):
        return ""
    return "%d|%s|" % (st.st_size, formatdate(st.st_mtime, usegmt=True))


def src_fps(keys):
    """批量取 kk:srcfp:<fk>（转换成功时记下的源文件指纹）。"""
    if not keys:
        return {}
    out = {}
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        try:
            p2 = subprocess.run(
                ["docker", "exec", "-i", "kk-redis", "redis-cli", "-n", "3",
                 "mget"] + chunk,
                capture_output=True, timeout=60)
        except Exception:
            return {}
        vals = (p2.stdout or b"").decode("utf-8", "replace").splitlines()
        for k, v in zip(chunk, vals):
            v = v.strip()
            if v and v != "nil":
                out[k] = v
    return out


def stale(fk, url, saved):
    """"标了就绪，但源文件其实已经换过了"。

    为什么不能只看 kk:ready：那个标记的 TTL 是 7 天，文件被同名覆盖后它照样在，
    预热就会跳过它 —— 于是"变过的文件当天没预转"，早高峰第一个打开的人白等一次转换。
    实测 114 个 ready 文件里有 2 个属于这种状态（同名同大小覆盖、或大小+mtime 都变）。

    比对的是"大小 + Last-Modified"，与网关 revalidate 同一口径；只比前两段是因为
    网关存的可能是 HTTP 三段式（多一个 ETag），本地 stat 只能给两段。
    """
    old = saved.get("kk:srcfp:" + fk, "")
    cur = local_fp(url)
    if not old or not cur:
        return False          # 判断不了就不折腾，交给网关在过期时校验
    return old.split("|")[:2] != cur.split("|")[:2]



def mysql(sql):
    """查库。必须指定 utf8mb4 并按字节读再解码：
    mysql 客户端的默认字符集不是 UTF-8，中文文件名会直接喂出
    'utf-8' codec can't decode byte 0xa0 这种错（第一次就踩了）。"""
    p = subprocess.run(
        ["docker", "exec", "kk-mysql", "mysql", "-ukkfileview", "-pchange-me-db-password",
         "kkfileview", "--default-character-set=utf8mb4", "-N", "-e", sql],
        capture_output=True, timeout=120)
    out = (p.stdout or b"").decode("utf-8", "replace")
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace")
        raise RuntimeError("查询失败: %s" % err.strip()[:300])
    return out.strip()


def hot_files(days, top, exclude_exts=()):
    """按"成功打开次数"取热点文件，排除直出型与用户指定的扩展名。

    **排除发生在排名内、LIMIT 之前**（不是取完 500 再筛）：这样 500 个名额
    全部落在需要转码的文件上，而不是被一半秒开的 PDF 占掉。

    源地址三处兜底（audit_log → preview_task）：前者覆盖所有类型，后者只覆盖
    需要转换的文件；两边都没有的（极少数，比如历史任务被清过）直接跳过。

    返回 (files, 查不到地址的个数, 排名总数, 其中直出型个数)。
    """
    rank = ("SELECT file_key, COUNT(*) n, MAX(file_name) name, MAX(file_ext) file_ext"
            " FROM access_log"
            " WHERE created_at > NOW() - INTERVAL %d DAY"
            "   AND success=1 AND file_key <> ''"
            " GROUP BY file_key ORDER BY n DESC LIMIT %d" % (days, top))
    direct = ",".join("'%s'" % e for e in DIRECT_EXTS)

    # 先量一下这 N 个热点里有多少是直出型，只为在日志里说清楚"为什么少了这么多"
    total, n_direct = 0, 0
    try:
        cnt = mysql("SELECT COUNT(*), IFNULL(SUM(CASE WHEN t.file_ext IN (%s)"
                    " THEN 1 ELSE 0 END),0)"
                    " FROM (%s) t" % (direct, rank))
        if cnt:
            p = cnt.splitlines()[0].split("\t")
            total = int(p[0] or 0)
            n_direct = int(p[1] or 0)
    except Exception:
        total = 0            # 统计失败不影响主流程

    excl = list(DIRECT_EXTS) + [e.strip().lower() for e in exclude_exts if e.strip()]
    skip = " WHERE (t.file_ext IS NULL OR t.file_ext NOT IN (%s))" % ",".join(
        "'%s'" % e for e in excl)
    sql = (
        "SELECT t.file_key, t.n, t.name, COALESCE(u.url, p.url) url, t.file_ext"
        " FROM (%s) t"
        " LEFT JOIN (SELECT file_key, MAX(file_url) url FROM audit_log"
        "            WHERE created_at > NOW() - INTERVAL %d DAY"
        "              AND file_url <> '' GROUP BY file_key) u"
        "   ON u.file_key = t.file_key"
        " LEFT JOIN (SELECT file_key, MAX(file_url) url FROM preview_task"
        "            GROUP BY file_key) p"
        "   ON p.file_key = t.file_key"
        "%s"
        " ORDER BY t.n DESC" % (rank, days, skip))
    out, no_url = [], 0
    for line in mysql(sql).splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        if len(parts) < 5:
            parts.append("")
        if not parts[3]:
            no_url += 1
            continue
        out.append({"fk": parts[0], "opens": int(parts[1] or 0),
                    "name": parts[2], "url": parts[3],
                    "ext": parts[4] or "?"})
    return out, no_url, total, n_direct


def ready_keys(fks):
    """批量问 Redis：哪些 fileKey 已经标过就绪。

    不先筛一遍的话，每个文件都要走一次 preview/url，而缓存过期（TTL 60 秒）后
    接口会先 HEAD 源站比对指纹——实测 5 个文件要 40 秒，300 个就是半小时。
    直接查 kk:ready:<fk>（TTL 7 天）几毫秒就能排掉绝大多数。
    """
    if not fks:
        return set()
    hit = set()
    for i in range(0, len(fks), 200):
        chunk = fks[i:i + 200]
        try:
            p2 = subprocess.run(
                # 必须用 raw 输出：--no-raw 会给每行加 "1) " 前缀，
                # 缺失的键显示成 "2) (nil)"，非空判断会把它们全当成命中
                ["docker", "exec", "-i", "kk-redis", "redis-cli", "-n", "3",
                 "mget"] + ["kk:ready:" + k for k in chunk],
                capture_output=True, timeout=60)
        except Exception:
            return set()
        vals = (p2.stdout or b"").decode("utf-8", "replace").splitlines()
        for k, v in zip(chunk, vals):
            if v.strip() and v.strip() != "nil":
                hit.add(k)
    return hit


def recent_failed(fks):
    """{fk: 错误}：最近 3 天最后一次转换任务是 failed 的文件。

    为什么要提前查：预热用 waitSeconds=0 发完就走，失败时脚本已经退出，
    事后复查也未必等得到（实测某个 pptx 要 260 秒才失败：下载超时 180s + 收尾，
    而复查窗口 180s）。这类文件每天都会被白触发一次、占一个并发槽。
    直接按"上次就失败了"提前跳过，一次 SQL 几毫秒，比触发一次 260 秒划算得多。

    **连接/超时/限流不算**：那是瞬时故障，按"3 天内失败过"封 3 天会误伤热点。
    实测 2026-11-12 源站抖动一次留下 8 条 failed 记录，第二天 5 个高频 xlsx/pptx
    全被跳过、白丢一天预热。这类交给 prestate 的 temp 挡（1h→6h→1d 按次退避），
    源站恢复了马上就能重试。
    """
    out = {}
    for i in range(0, len(fks), 200):
        chunk = fks[i:i + 200]
        inlist = ",".join("'%s'" % k for k in chunk)
        sql = ("SELECT t.file_key, IFNULL(t.err_msg,'') FROM preview_task t"
               " JOIN (SELECT file_key, MAX(created_at) mx FROM preview_task"
               "       WHERE file_key IN (%s)"
               "         AND created_at > NOW() - INTERVAL 3 DAY"
               "       GROUP BY file_key) m"
               " ON m.file_key=t.file_key AND m.mx=t.created_at"
               " WHERE t.status='failed'") % inlist
        try:
            rows = mysql(sql)
        except Exception:
            return out
        for line in rows.splitlines():
            p = line.split("\t")
            if len(p) >= 2 and p[0]:
                err = p[1] or "转换失败"
                if prestate.kind_of(err) == "temp":
                    continue          # 瞬时故障，交给 temp 冷却按次退避
                out[p[0]] = err
    return out


def task_state(fk):
    """该文件最新一条转换任务的状态（ready/failed/converting/queued/空）。"""
    try:
        out = mysql("SELECT status FROM preview_task WHERE file_key='%s'"
                    " ORDER BY created_at DESC LIMIT 1" % fk)
    except Exception:
        return ""
    return out.strip().splitlines()[0] if out.strip() else ""


def task_error(fk):
    try:
        out = mysql("SELECT IFNULL(err_msg,'') FROM preview_task WHERE file_key='%s'"
                    " ORDER BY created_at DESC LIMIT 1" % fk)
    except Exception:
        return ""
    return out.strip().splitlines()[0] if out.strip() else ""


def one(item, tk):
    """触发一次 preview/url。缓存命中的会立刻返回 ready，冷的会后台开转。"""
    try:
        r = pc.http_json("/api/v1/preview/url",
                         {"fileUrl": item["url"], "empNo": EMP,
                          "waitSeconds": 0}, tk, timeout=120)
        d = r.get("data") or {}
        warm = d.get("status") == "ready" and not d.get("provisional")
        return ("warm" if warm else "cold", item, "")
    except Exception as exc:
        # 记冷却，明天/下周不再重复撞同一堵墙。kind_of 会把 403/404 归成 notfound
        # （冷却 1d→7d→90d），超时/5xx 归成 temp（1h→6h→1d）。
        try:
            prestate.note_fail(item.get("fk_calc", ""), prestate.kind_of(exc), exc,
                               local_fp(item["url"]))
        except Exception:
            pass
        return ("fail", item, str(exc)[:120])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10, help="统计最近多少天的打开次数")
    ap.add_argument("--top", type=int, default=500, help="预热多少个热点文件")
    ap.add_argument("--workers", type=int, default=2,
                    help="并发数（别超过 LO 并发）。3 并发拉 60 个文件时会打回 429，降到 2")
    ap.add_argument("--skip-ext", default="",
                    help="额外排除的扩展名（逗号分隔）。直出型（PDF/图片/文本/音视频）"
                         "已默认排除；压缩包预热会把整个包下载一遍，带宽紧张时可加 zip,rar,7z")
    ap.add_argument("--verbose", action="store_true",
                    help="打印失败明细（默认只报数量）")
    ap.add_argument("--refresh", action="store_true",
                    help="不查 Redis 就绪标记，全部走一遍接口（GC 之后用）")
    ap.add_argument("--no-recheck", action="store_true",
                    help="不复查刚触发的转换（默认等最多 120 秒，失败则记冷却）")
    # 默认 180 秒：转换里下载的超时就是 180 秒，等短了看不到"连接失败"这个结果
    ap.add_argument("--recheck-sec", type=int, default=180,
                    help="复查等待上限（秒）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    skip = tuple(e for e in (a.skip_ext or "").split(",") if e.strip())
    files, no_url, total, n_direct = hot_files(a.days, a.top, skip)
    if not files:
        print("最近 %d 天没有可预热记录" % a.days)
        return 0
    print("近 %d 天的热点排名前 %d 个：" % (a.days, total or a.top), end="")
    if n_direct:
        print("其中 %d 个是直出型（PDF/图片/文本/音视频，不转码、预热无收益）已跳过，"
              % n_direct, end="")
    print("实际预热 %d 个（打开 %d ~ %d 次）"
          % (len(files), files[-1]["opens"], files[0]["opens"]))
    if no_url:
        print("  其中 %d 个查不到源地址（审计与任务表都没有），跳过" % no_url)
    if a.dry_run:
        for f in files[:15]:
            print("  %5d 次  %-6s %s" % (f["opens"], f["ext"][:6], (f["name"] or f["url"])[:60]))
        print("dry-run，未触发任何转换")
        return 0

    tk = pc.http_json("/api/v1/auth/token",
                      {"appId": "kk-demo", "appSecret": "your-app-secret"}
                      )["data"]["accessToken"]

    if not a.refresh:
        for f in files:
            f["fk_calc"] = pc.file_key(f["url"])
        fks = [f["fk_calc"] for f in files]
        hot = ready_keys(fks)
        saved = src_fps(["kk:srcfp:" + k for k in fks])
        todo, changed = [], []
        for f in files:
            if f["fk_calc"] not in hot:
                todo.append(f)
            elif stale(f["fk_calc"], f["url"], saved):
                # 标了就绪但源已换过：必须再走一遍，否则今天它一直是旧内容
                f["stale"] = True
                changed.append(f)
                todo.append(f)
        print("已就绪 %d 个（其中 %d 个源文件已变，需重新预热），需要处理 %d 个"
              % (len(files) - len(todo), len(changed), len(todo)))
        if changed and a.verbose:
            for f in changed[:15]:
                print("  源已变  %-6s %s" % (f["ext"][:6], (f["name"] or "")[:56]))

        # 失败冷却：403（业务接口要用户会话）/404（源文件已删）/加密的文件，
        # 每天都会被重新探测一遍，日志里天天是同一批 16 个失败。
        # 复用 preconvert 那套 kk:pre:fail:<fk>（1d→7d→90d），源文件变了自动解除。
        states = prestate.load_many([f["fk_calc"] for f in todo])
        keep, cool = [], 0
        for f in todo:
            st = states.get(f["fk_calc"])
            if st:
                fp = local_fp(f["url"])
                # 与 prestate.blocked 同一条规则：指纹变了说明换过文件，立刻重试
                if not (fp and st.get("fp") and st["fp"] != fp) \
                        and time.time() < st.get("until", 0):
                    cool += 1
                    if a.verbose:
                        print("  冷却中  %-6s %s"
                              % (f["ext"][:6], (f["name"] or "")[:52]))
                    continue
            keep.append(f)
        if cool:
            print("其中 %d 个在失败冷却中（源文件不可达/加密，换过文件会自动解除），跳过"
                  % cool)

        # 冷却之外再看一层：最近 3 天转换失败过的（源站不可达、加密）不再触发，
        # 直接补记冷却。冷却有 TTL，隔天就过期，光靠它挡不住"每天撞一次"。
        staled = recent_failed([f["fk_calc"] for f in keep])
        if staled:
            kept2 = []
            for f in keep:
                err = staled.get(f["fk_calc"])
                if err:
                    try:
                        prestate.note_fail(f["fk_calc"], prestate.kind_of(err), err,
                                           local_fp(f["url"]))
                    except Exception:
                        pass
                    print("  近期转换失败，跳过并续冷却  %-6s %s  (%s)"
                          % (f["ext"][:6], (f["name"] or "")[:40], err[:36]))
                    continue
                kept2.append(f)
            print("其中 %d 个最近转换失败过，跳过" % (len(keep) - len(kept2)))
            keep = kept2

        files = keep
        if not files:
            print("全部命中缓存、冷却中或近期失败过，无需预热")
            return 0

    stat = {"warm": 0, "cold": 0, "fail": 0}
    fails = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
        futs = [pool.submit(one, f, tk) for f in files]
        for i, fut in enumerate(as_completed(futs), 1):
            kind, item, err = fut.result()
            stat[kind] += 1
            if kind == "fail":
                fails.append((item["name"] or item["url"], item.get("ext", "?"), err))
            elif kind == "cold":
                item["triggered"] = True
                if stat["cold"] <= 10:
                    print("  触发转换 %4d 次打开  %-6s %s"
                          % (item["opens"], item["ext"][:6],
                             (item["name"] or item["url"])[:56]))
            if i % 50 == 0:
                print("  ...已处理 %d/%d（冷 %d）" % (i, len(files), stat["cold"]))

    print("\n完成：命中缓存 %d / 触发转换 %d / 失败 %d，用时 %.1f 秒"
          % (stat["warm"], stat["cold"], stat["fail"], time.time() - t0))
    if fails:
        # 这些是"最近 10 天有人成功打开过、但现在打不开"的源文件。常见两类：
        #   403 —— 业务系统自己的接口（127.0.0.1:8080/api/…）需要用户会话，
        #          服务器没有凭据，永远拉不到，这类只能靠业务侧预热或干脆跳过；
        #   404 —— 文件已被移动/删除，索引与磁盘不一致。
        # 默认只报数量：每天十几行明细会把日志淹掉，要查时加 --verbose。
        print("失败 %d 个%s" % (len(fails),
              "" if a.verbose else "（加 --verbose 看明细）"))
    if fails and a.verbose:
        print("失败明细：")
        for name, ext, err in fails[:20]:
            short = err.split("。")[0].replace("HTTP 400: ", "")[:70]
            print("  %-6s %-46s %s" % (ext[:6], name[:46], short))
        if len(fails) > 20:
            print("  ……另有 %d 个" % (len(fails) - 20))
    # 复查刚触发的转换：源文件不可达/加密的文件会一直失败，而预热是"发完就走"
    # （waitSeconds=0），失败时脚本早退出了，所以它永远进不了冷却 —— 实测某个
    # pptx 源站长期不可达，每天预热都白触发一次、卡满 180 秒下载超时。
    # 这里等一会儿看结果，失败就记进 prestate 冷却，明天直接跳过。
    check = [f for f in files if f.get("triggered")]
    if check and not a.no_recheck:
        print("复查 %d 个刚触发的转换（最多等 %ds，--no-recheck 可跳过）"
              % (len(check), a.recheck_sec))
        deadline = time.time() + a.recheck_sec
        pending = {f["fk_calc"]: f for f in check}
        while pending and time.time() < deadline:
            time.sleep(10)
            for fk in list(pending):
                st = task_state(fk)
                if st not in ("ready", "failed"):
                    continue
                f = pending.pop(fk)
                if st == "failed":
                    err = task_error(fk) or "转换失败"
                    try:
                        prestate.note_fail(fk, prestate.kind_of(err), err,
                                           local_fp(f["url"]))
                    except Exception:
                        pass
                    print("  转换失败并已冷却  %-6s %s  (%s)"
                          % (f["ext"][:6], (f["name"] or "")[:44], err[:40]))
        if pending:
            print("  还有 %d 个没落定（超过 %ds），下次预热再复查"
                  % (len(pending), a.recheck_sec))
    print("触发的转换在后台继续跑，可用 /health 的 converting 观察队列。")
    # 有失败不代表整体失败：源文件可能已经删了（403/404），下次统计自然会剔除
    return 0


if __name__ == "__main__":
    sys.exit(main())
