#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CAD 预转：把受控文件里的 DWG/DXF 提前转好，用户点开时直接命中缓存。

**为什么必须预转**

Aspose 解析 7.4MB 图纸要 4 秒（12.6 万个实体），光栅化 5.5 秒，转矢量 PDF 13 秒。
无论怎么优化现场转换，都不可能进 3 秒 —— 唯一办法是"用户点之前就转好"。

受控文件里 DWG 只有 34 个（STEP 有 1000+，那是浏览器解析，另说），
全量预转一次十几分钟，之后靠缓存保活续期，任何时候打开都是 1 秒内。

**怎么绕过签名**

业务 URL 带 `exp`/`sig`，5 分钟就过期，预转时没法用。所以：

    扫 Ceph 本地文件 → 按业务 URL 的规则算出 fileKey（已验证与线上一致）
                    → 把文件放到本地静态服务，用本地 URL 让引擎真转一遍
                    → 把产物按业务 fileKey 落盘 + 标 ready

产物文件名只取决于 fileKey，与源 URL 无关，所以用户点业务链接时直接命中。

用法（在 127.0.0.1 宿主机上跑）：

    python3 /opt/kkfileview-service/tools/preconvert.py --ext dwg
    python3 /opt/kkfileview-service/tools/preconvert.py --ext dwg,dxf --limit 20
    python3 /opt/kkfileview-service/tools/preconvert.py --ext dwg --dry-run
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import formatdate

# 失败状态与冷却：源文件已被删（404）的文件不该每小时被重试一次。
# 见 prestate.py 文件头（实测 49 个文件被重试了 272 轮）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prestate  # noqa: E402

ROOT = "/cephfs_data/fileManagement/files/示例事业部/文档中心/受控文件"
BIZ_BASE = "http://127.0.0.1:8080/api/file/raw"
LOCAL_BASE = "http://127.0.0.1:8899"
API = "http://127.0.0.1:9012"
STATIC = "/opt/kkfileview-service/samples"
SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
SKIP_WORDS = ("旧版本",)

VOLATILE = {
    "exp", "expires", "sig", "sign", "signature", "token", "access_token",
    "auth", "authorization", "timestamp", "ts", "_t", "nonce",
}


def _load_filekey():
    """按文件路径加载网关的 filekey.py（不走 package，免得拉起 httpx 等依赖）。

    这个函数以前在这里有一份手抄副本，注释还写着"与 engine.py 完全一致"——
    只要网关那边改了归一化规则，这里就静默算错，预转全部落空。
    """
    import importlib.util
    cand = [
        "/opt/kkfileview-service/gateway/app/filekey.py",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "gateway", "app", "filekey.py"),
    ]
    for p in cand:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("kk_filekey", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("找不到 gateway/app/filekey.py（预转与网关必须共用同一份 key 算法）")


_filekey = _load_filekey()


def file_key(url: str) -> str:
    """缓存键 = 文件身份。实现见 gateway/app/filekey.py（与网关共用）。"""
    return _filekey.of(url)


def http_json(path, body=None, token=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data,
        headers={"Content-Type": "application/json", "X-App-Id": "kk-demo",
                 **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # 原来只抛 "HTTP Error 400: Bad Request"，看不出是源文件没了还是格式不支持，
        # 日志里 5872 条全是这一句。带上接口的 detail（"文件地址不可访问(HTTP 404)"），
        # 才能被 prestate.kind_of() 归类成 notfound 并进入长冷却。
        detail = ""
        try:
            detail = (json.loads(e.read().decode("utf-8", "replace")) or {}).get("detail") or ""
        except (ValueError, TypeError):
            pass
        raise RuntimeError("HTTP %s%s" % (e.code, (": " + str(detail)) if detail else ""))


def src_fp(path):
    """源文件指纹：大小 + mtime(GMT)。必须与 gateway/app/engine.local_fingerprint
    算出来的字符串**逐字节相同**，否则网关会判定"文件变了"，白转一遍。"""
    st = os.stat(path)
    return "%d|%s|" % (st.st_size, formatdate(st.st_mtime, usegmt=True))


def listdir_safe(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def redis(*args):
    return sh("docker exec kk-redis redis-cli -n 3 " + " ".join(args)).stdout.strip()


def mysql(sql):
    return sh('docker exec kk-mysql mysql -ukkfileview -pchange-me-db-password kkfileview -N -e '
              + shlex.quote(sql)).stdout.strip()


def scan(exts, limit):
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        if any(w in dirpath for w in SKIP_WORDS):
            continue
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            if ext not in exts:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if any(w in rel for w in SKIP_WORDS):
                continue
            out.append((full, rel, fn))
            if limit and len(out) >= limit:
                return out
    return out


def already(fk, src_path):
    """只跳过"产物在 **且** 源文件没变"的。

    原来的判断只看"产物存在"，于是源文件改过之后（只要产物还没过期），
    定时任务会一直跳过它 —— 用户看到的一直是旧图纸，直到缓存过期才有人发现。
    """
    if redis("get", "kk:ready:" + fk) not in ("cad", "pdf"):
        return False
    if not (os.path.exists(os.path.join(SHEETS, fk + ".cad.pdf"))
            or os.path.exists(os.path.join(SHEETS, fk + ".cad_p0_w1600.png"))):
        return False
    saved = redis("get", "kk:srcfp:" + fk)
    return bool(saved) and saved == src_fp(src_path)


def warm_png(fk, biz_url, name, tk, widths=(1600, 2400)):
    """把一份图纸的首屏位图渲出来（没有 PDF 或已有位图就跳过）。

    预转只搬了 PDF；用户第一次打开时网关还要用 PDFium 渲一页（7.4MB 图纸 1.8 秒）。
    提前渲好，首屏就只剩"取一张 118KB 的图"。
    """
    if not os.path.exists(os.path.join(SHEETS, fk + ".cad.pdf")):
        return "no-pdf"
    if os.path.exists(os.path.join(SHEETS, fk + ".cad_p0_w%d.png" % widths[-1])):
        return "exists"
    d = http_json("/api/v1/preview/url",
                  {"fileUrl": biz_url, "empNo": "PRECONV",
                   "empName": "预转换", "fileName": name}, tk)
    pv = (d.get("data") or {}).get("previewUrl") or ""
    tok = pv.split("token=")[-1] if "token=" in pv else ""
    if not tok:
        raise RuntimeError(d.get("msg") or "拿不到预览令牌")
    for w in widths:
        u = "%s/cadpng/%s?kkt=%s&w=%d&p=0" % (API, fk, urllib.parse.quote(tok), w)
        with urllib.request.urlopen(u, timeout=300) as r:
            r.read()
    return "ok"


def warm_pngs(files, tk):
    ok = skip = fail = 0
    for i, (full, rel, name) in enumerate(files, 1):
        biz_url = "%s/%s?path=%s" % (BIZ_BASE, urllib.parse.quote(name),
                                     urllib.parse.quote(rel, safe="/"))
        tag = "[%d/%d] %s" % (i, len(files), name[:44])
        try:
            st = warm_png(file_key(biz_url), biz_url, name, tk)
        except Exception as exc:
            fail += 1
            print("%s 失败: %s" % (tag, exc), flush=True)
            continue
        if st == "ok":
            ok += 1
            print("%s 位图已渲" % tag, flush=True)
        else:
            skip += 1
    print("\n位图生成 %d / 跳过 %d / 失败 %d" % (ok, skip, fail))
    return 0 if not fail else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", default="dwg", help="逗号分隔，默认 dwg")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略缓存全部重转（怀疑有更新没被发现时用）")
    ap.add_argument("--png-only", action="store_true",
                    help="只补首屏位图（产物已在、首屏还没生成时用）")
    ap.add_argument("--verbose", action="store_true",
                    help="连\"已就绪\"的跳过也打印（默认只输出完成/失败/汇总）")
    a = ap.parse_args()
    exts = {e.strip().lower() for e in a.ext.split(",") if e.strip()}

    files = scan(exts, a.limit)
    print("待处理 %d 个文件（ext=%s）" % (len(files), ",".join(sorted(exts))), flush=True)
    tmpdir = os.path.join(STATIC, "_pre")
    os.makedirs(tmpdir, exist_ok=True)

    tk = http_json("/api/v1/auth/token",
                   {"appId": "kk-demo", "appSecret": "your-app-secret"})["data"]["accessToken"]

    if a.png_only:
        return warm_pngs(files, tk)

    stat = {"done": 0, "skip": 0, "cool": 0, "fail": 0, "loud": 0}
    for i, (full, rel, name) in enumerate(files, 1):
        biz_url = "%s/%s?path=%s" % (BIZ_BASE, urllib.parse.quote(name),
                                     urllib.parse.quote(rel, safe="/"))
        fk = file_key(biz_url)
        tag = "[%d/%d] %s" % (i, len(files), name[:46])
        if a.dry_run:
            print("%s -> %s" % (tag, fk[:16]), flush=True)
            continue
        try:
            fp = src_fp(full)
        except OSError as exc:
            stat["fail"] += 1
            print("%s 读不到源文件: %s" % (tag, exc), flush=True)
            continue

        # 处于冷却期：静默跳过（不复制、不调接口、不写日志）。
        # 源文件指纹变了会自动解除，不用手工干预。
        cool = prestate.blocked(fk, fp)
        if cool:
            stat["cool"] += 1
            continue

        if not a.force and already(fk, full):
            # 默认静默：已就绪的占绝大多数，每轮逐个打印只会淹掉失败信息
            if a.verbose:
                print("%s 已就绪且源文件未变，跳过" % tag, flush=True)
            stat["skip"] += 1
            continue

        ext = os.path.splitext(name)[1]
        # 临时文件名带 pid：定时任务和手动跑同时进行时，两个进程会互相删掉对方的
        # 临时文件（错误长这样：No such file or directory: .../_pre/<fk>.dxf）。
        # 【注意】URL 必须用**同一个**文件名。这里原来写的是 "<fk><ext>"，少了 ".pid"，
        # 静态服务上根本不存在这个文件 → 每轮 100% 404「文件地址不可访问」，
        # 而且被失败冷却归成 notfound，看起来像"源文件被删了"，实际是本地 URL 拼错。
        lname = "%s.%d%s" % (fk, os.getpid(), ext)
        local = os.path.join(tmpdir, lname)
        try:
            shutil.copyfile(full, local)
            lurl = "%s/_pre/%s" % (LOCAL_BASE, lname)
            t0 = time.time()
            d = http_json("/api/v1/preview/url",
                          {"fileUrl": lurl, "empNo": "PRECONV", "empName": "预转换",
                           "fileName": name, "wait": True, "waitSeconds": 600}, tk)
            if d.get("code") != 0:
                raise RuntimeError(d.get("msg"))
            data = d["data"]
            if data.get("status") != "ready":
                raise RuntimeError("未就绪: " + str(data.get("status")))
            cost = time.time() - t0

            lfk = data["fileKey"]
            moved = 0
            for fn in os.listdir(SHEETS):
                if not fn.startswith(lfk + "."):
                    continue
                suffix = fn[len(lfk):]                    # ".cad.pdf" / ".cad_p0_w1600.png"
                dst = os.path.join(SHEETS, fk + suffix)
                shutil.copyfile(os.path.join(SHEETS, fn), dst)
                os.utime(dst, None)                       # 缓存年龄从现在算
                moved += 1
            if not moved:
                raise RuntimeError("没有可搬的产物")

            redis("set", "kk:ready:" + fk, "cad", "EX", "604800")
            # 指纹用 Ceph 上的 size + mtime：源站 nginx 的 Last-Modified 就是文件 mtime，
            # 两边一致时保活可以一直续期，不用重转。
            redis("set", "kk:srcfp:" + fk, shlex.quote(src_fp(full)),
                  "EX", str(86400 * 30))
            mysql("DELETE FROM preview_task WHERE file_key='%s'" % fk)
            mysql("INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url,"
                  " file_name, status, ready_at) VALUES ('%s','_all','PRECONV','%s','%s',"
                  " '%s','ready',NOW())"
                  % (hashlib.md5(fk.encode()).hexdigest(), fk,
                     biz_url.replace("'", "''"), name.replace("'", "''")[:400]))
            # 转换是用"本地 URL"发起的，那一次在 sheets 和 preview_task 里留下了
            # 一套本地 fk 的产物/记录。业务 fk 已经有一份，留着就是双倍磁盘
            # （7.4MB 布局图每份 PDF 46MB）。这里顺手清掉。
            if lfk != fk:
                for fn in listdir_safe(SHEETS):
                    if fn.startswith(lfk + "."):
                        try:
                            os.remove(os.path.join(SHEETS, fn))
                        except OSError:
                            pass
                mysql("DELETE FROM preview_task WHERE file_key='%s'" % lfk)
            stat["done"] += 1
            prestate.note_ok(fk)
            extra = ""
            try:
                st = warm_png(fk, biz_url, name, tk)
                extra = {"ok": " + 首屏位图",
                         "exists": " （位图已存在）",
                         "no-pdf": " （无 PDF，跳过位图）"}.get(st, "")
            except Exception as exc:
                extra = " （位图失败: %s）" % str(exc)[:50]
            print("%s 完成 %.1fs  产物 %d 个%s" % (tag, cost, moved, extra), flush=True)
        except Exception as exc:
            stat["fail"] += 1
            st, loud = prestate.note_fail(
                fk, prestate.kind_of(exc), exc, fp)
            if loud:
                # 第一次失败、或冷却期升级（1d→7d→90d）才打明细。
                # 已经被判定"源文件没了"的文件，之后每轮只是一行汇总。
                print("%s 失败(第 %d 次，%s): %s  → 下次 %s 之后再试"
                      % (tag, st["n"],
                         prestate.KIND_CN.get(st["kind"], st["kind"]),
                         str(exc)[:140],
                         time.strftime("%m-%d %H:%M", time.localtime(st["until"]))),
                      flush=True)
        finally:
            # 临时拷贝无论成败都要删：失败路径原来不删，_pre 里堆了 6026 个文件 / 37GB，
            # 而且全是同名 fk 的历史版本，越积越大。
            if os.path.exists(local):
                try:
                    os.remove(local)
                except OSError:
                    pass

    print("\n完成 %d / 跳过 %d / 冷却 %d / 失败 %d"
          % (stat["done"], stat["skip"], stat["cool"], stat["fail"]))
    if stat["cool"]:
        print("冷却中的文件不重复重试（源文件变化会自动解除）。"
              "查看: python3 tools/prestate.py --list")
    return 0 if not stat["fail"] else 1


if __name__ == "__main__":
    sys.exit(main())
