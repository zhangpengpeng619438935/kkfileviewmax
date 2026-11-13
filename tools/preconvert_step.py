#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STEP/IGES 预解析：把 3D 实体离线转成网格，用户点开时只下载 + 画。

**为什么必须预解析**

引擎的做法是在浏览器里用 WASM 单线程解析 STEP：14MB 实测 19.5 秒，47MB 要 2~5 分钟，
期间页面只有"加载中"。这段耗时与终端 CPU 直接相关，且每次打开都要重来。
服务端用同一套 OCCT（tools/stepmesh/step2mesh.js）解析一次存成网格（14MB → 4.7MB，
gzip 后约 1.5MB），浏览器只剩"下载 + 建 BufferGeometry"，实测 1~2 秒。

**用法（127.0.0.1 宿主机）**

    python3 /opt/kkfileview-service/tools/preconvert_step.py --limit 5
    python3 /opt/kkfileview-service/tools/preconvert_step.py --max-mb 5     # 只转小文件
    python3 /opt/kkfileview-service/tools/preconvert_step.py --list         # 只看清单

每个文件 1~20 秒（取决于体积）。建议分批发：先小文件（中位数 356KB，1~2 秒），
再挑大的；跑完靠保活续期，不用重复跑。
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# file_key / BIZ_BASE 与 DWG 预转共用一份实现：两边算出来的键必须逐个字节相同，
# 否则产物落不到用户点开时用的那个键上（复制两份代码迟早会漂）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preconvert import BIZ_BASE, ROOT, file_key, src_fp  # noqa: E402
import prestate  # noqa: E402
import step_wire  # noqa: E402

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
MESHER = "/opt/kkfileview-service/tools/stepmesh/step2mesh.js"
# 网格 gz 的最小有效体积。空装配体（只有坐标系/基准面/曲线，没有实体面）
# 导出的 gz 只有 115 字节；以前这个值不满足下面"已是最新"的 1024 判定，
# 于是每轮都重新解析（单文件 100 秒），38 个文件把一轮拖到 110 分钟。
# 真实网格最小也有几十 KB，2KB 足够区分。
MIN_MESH_BYTES = 2048
ASSEMBLE = "/opt/kkfileview-service/tools/step_assemble.py"
# 多大的 STEP 才允许走"拆件拼装"。小文件失败通常是几何本身有问题，
# 拆一遍要几分钟，不值得。0 = 关闭。
ASSEMBLE_MAX_MB = int(os.environ.get("STEP_ASSEMBLE_MAX_MB", "400"))
SKIP_WORDS = ("旧版本",)
EXTS = {"step", "stp", "iges", "igs", "brep", "fcstd", "fcstd1"}


def _quat_mul(a, b):
    """四元数乘法 (x,y,z,w)。a 在外、b 在内。"""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _quat_rot(q, v):
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        (1 - 2 * (y * y + z * z)) * v[0] + 2 * (x * y - z * w) * v[1] + 2 * (x * z + y * w) * v[2],
        2 * (x * y + z * w) * v[0] + (1 - 2 * (x * x + z * z)) * v[1] + 2 * (y * z - x * w) * v[2],
        2 * (x * z - y * w) * v[0] + 2 * (y * z + x * w) * v[1] + (1 - 2 * (x * x + y * y)) * v[2],
    ]


def fcstd_parts(path):
    """把 FreeCAD 文档拆成「一堆 .brp + 每个零件的位姿」。

    FCStd 是个 zip：`Document.xml`（对象清单 + 每个对象的属性）+ 若干
    `XXX.Shape.brp`（OCCT 的 BRep 几何）。

    **位姿是父子链相乘的**：零件可能挂在一层层 App::Part / Assembly 下面，
    链上每级都有自己的 Placement。只取零件自身的 Placement 会漏掉容器的位移。
    Placement 的四元数顺序是 (Q0,Q1,Q2,Q3) = (x, y, z, w)。

    **但要不要真的套上去，由 step2mesh.js 按数据判定**：有的文档把全局坐标
    直接烘进了 brp（这时再叠加会让零件整体炸开，实测最远零件 145mm -> 1529mm），
    有的则是局部坐标必须叠加。所以这里只负责把「候选位姿」算准，套不套由下游选。
    """
    import re as _re
    import tempfile
    import zipfile

    tmp = tempfile.mkdtemp(prefix="kkfc_")
    z = zipfile.ZipFile(path)
    try:
        doc = z.read("Document.xml").decode("utf-8", "replace")

        place, parent = {}, {}
        for m in _re.finditer(r'<Object name="([^"]+)">(.*?)</Object>', doc, _re.S):
            name, body = m.group(1), m.group(2)
            pm = _re.search(
                r'<PropertyPlacement Px="([^"]*)" Py="([^"]*)" Pz="([^"]*)" '
                r'Q0="([^"]*)" Q1="([^"]*)" Q2="([^"]*)" Q3="([^"]*)"', body)
            if pm:
                try:
                    place[name] = [float(v) for v in pm.groups()]
                except ValueError:
                    pass
            gm = _re.search(r'<Property name="Group"[^>]*>(.*?)</Property>', body, _re.S)
            if gm:
                for child in _re.findall(r'<Link value="([^"]+)"', gm.group(1)):
                    parent.setdefault(child, name)

        def world(obj):
            """从该对象往上乘到根：T = T_parent ∘ T_self。"""
            chain, cur, guard = [], obj, 0
            while cur and guard < 32:
                chain.append(cur)
                cur = parent.get(cur)
                guard += 1
            t = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
            for node in reversed(chain):           # 由外到内
                p = place.get(node)
                if not p:
                    continue
                rot = _quat_rot(t[3:], p[:3])
                t = [t[0] + rot[0], t[1] + rot[1], t[2] + rot[2]] + _quat_mul(t[3:], p[3:])
            return t if any(abs(v) > 1e-9 for v in t[:3]) or abs(t[6] - 1) > 1e-9 else None

        brps, tf = [], {}
        for n in z.namelist():
            if not n.endswith(".Shape.brp"):
                continue
            dst = os.path.join(tmp, os.path.basename(n))
            with open(dst, "wb") as fh:
                fh.write(z.read(n))
            brps.append(dst)
            obj = n[:-len(".Shape.brp")]
            t = world(obj)
            if t:
                tf[os.path.basename(n)] = t
        if not brps:
            raise RuntimeError("FCStd 里没有 Part.Shape.brp（可能是纯草图/网格模型）")
        tf_path = os.path.join(tmp, "_place.json")
        with open(tf_path, "w", encoding="utf-8") as fh:
            json.dump(tf, fh)
        return brps, tf_path, tmp
    finally:
        z.close()


def assemble_step(full, workdir, key=""):
    """整份 STEP 三角化为空时的兜底：按零件切开解析，再按装配位姿拼回来。

    为什么需要：文件里存在装配实例实体（NAUO / CDSR / ITEM_DEFINED_TRANSFORMATION）时，
    当前 occt-import-js 构建会把三角化**整体返回空**（实测 6708 个网格、0 顶点），
    而同样的几何去掉这些实体后立刻正常（12 个零件 → 28,482 个三角形）。
    所以这里改成逐零件解析、自己算位姿拼装。
    见 tools/step_assemble.py 与运维手册「二·四十四」。
    """
    if ASSEMBLE_MAX_MB <= 0:
        return None
    size_mb = os.path.getsize(full) / 1048576.0
    if size_mb > ASSEMBLE_MAX_MB:
        print("  跳过拆件拼装：%.0f MB 超过上限 %d MB" % (size_mb, ASSEMBLE_MAX_MB),
              flush=True)
        return None
    out = os.path.join(workdir, "asm")
    r = sh("python3 %s --outdir %s --key %s %s"
           % (ASSEMBLE, shlex.quote(out), shlex.quote(key or ""),
              shlex.quote(full)))
    path = os.path.join(out, "assembled.mesh")
    if r.returncode != 0 or not os.path.exists(path) \
            or os.path.getsize(path) < MIN_MESH_BYTES:
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-2:]
        print("  拆件拼装未成功：%s" % " / ".join(tail)[:160], flush=True)
        return None
    for line in (r.stdout or "").splitlines():
        if line.startswith("拼装完成") or line.startswith("包围盒"):
            print("  " + line, flush=True)
    return path


def sh(cmd, timeout=1800):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)


def redis(*args):
    return sh("docker exec kk-redis redis-cli -n 3 " + " ".join(args)).stdout.strip()


def mysql(sql):
    return sh('docker exec kk-mysql mysql -ukkfileview -pchange-me-db-password kkfileview -N -e '
              + shlex.quote(sql)).stdout.strip()


def scan(max_mb=0, min_mb=0, limit=0, name=""):
    out = []
    for dirpath, _dn, filenames in os.walk(ROOT):
        if any(w in dirpath for w in SKIP_WORDS):
            continue
        for fn in filenames:
            if os.path.splitext(fn)[1].lower().lstrip(".") not in EXTS:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT)
            if any(w in rel for w in SKIP_WORDS):
                continue
            if name and name.lower() not in fn.lower():
                continue
            mb = os.path.getsize(full) / 1048576.0
            if max_mb and mb > max_mb:
                continue
            if min_mb and mb < min_mb:
                continue
            out.append((full, rel, fn, mb))
    out.sort(key=lambda x: x[3])          # 小的先来，先覆盖大多数
    return out[:limit] if limit else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-mb", type=float, default=0)
    ap.add_argument("--min-mb", type=float, default=0)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略已有网格全部重解析")
    ap.add_argument("--name", default="", help="只处理文件名包含该串的")
    ap.add_argument("--verbose", action="store_true",
                    help="连\"已是最新\"的跳过也打印（默认只输出完成/失败/汇总）")
    a = ap.parse_args()

    files = scan(a.max_mb, a.min_mb, a.limit, a.name)
    print("待解析 %d 个（总计 %.1f MB）" % (len(files), sum(f[3] for f in files)), flush=True)
    if a.list:
        for full, rel, fn, mb in files:
            print("  %7.2f MB  %s" % (mb, rel))
        return 0

    ok = skip = fail = enc = cool = empty = wired = assembled = 0
    t_all = time.time()
    for i, (full, rel, fn, mb) in enumerate(files, 1):
        biz_url = "%s/%s?path=%s" % (BIZ_BASE, urllib.parse.quote(fn),
                                     urllib.parse.quote(rel, safe="/"))
        fk = file_key(biz_url)
        tag = "[%d/%d] %5.1fMB %s" % (i, len(files), mb, fn[:44])
        gz_path = os.path.join(SHEETS, fk + ".stepmesh.gz")
        try:
            fp = src_fp(full)
        except OSError as exc:
            fail += 1
            print("%s 读不到源文件: %s" % (tag, exc), flush=True)
            continue

        # 冷却期内的文件静默跳过。空网格（gz 115 字节）以前每轮都重新解析一遍，
        # 38 个文件就烧掉 110 分钟；现在第一次失败进冷却，之后只有汇总里计数。
        cool_msg = prestate.blocked(fk, fp)
        if cool_msg:
            cool += 1
            continue

        if os.path.exists(gz_path) and os.path.getsize(gz_path) > MIN_MESH_BYTES:
            # 网格在 ≠ 模型没改：源文件变了必须重解析，否则用户一直看到旧装配体
            saved = redis("get", "kk:srcfp:" + fk)
            if not a.force and saved == fp:
                # 默认不打印：1025 个已转好的文件每轮各打一行，日志一轮就 96KB，
                # 真正要看的失败信息反而被淹掉。需要时加 --verbose。
                if a.verbose:
                    print("%s 网格已是最新，跳过" % tag, flush=True)
                skip += 1
                continue
        raw = os.path.join(SHEETS, fk + ".stepmesh")
        ext = os.path.splitext(fn)[1].lower()
        # 文档加密系统包裹的文件（%TSD-Header-###%）整份是密文，OCCT 只会报
        # "no meshes"。以前每轮都打一行，现在记进冷却（永久性无效，等明文版）。
        try:
            with open(full, "rb") as fh:
                if fh.read(16).startswith(b"%TSD"):
                    st, loud = prestate.note_fail(
                        fk, "notfound", "文件被文档加密系统包裹", fp)
                    enc += 1
                    if loud:
                        print("%s 加密文件（第 %d 次）：服务器无解密能力，"
                              "请让文档所有者导出明文版" % (tag, st["n"]), flush=True)
                    continue
        except OSError:
            pass
        try:
            t0 = time.time()
            if ext == ".fcstd" or ext == ".fcstd1":
                brps, tf_path, tmpdir = fcstd_parts(full)
                try:
                    cmd = "node %s -o %s --tf %s %s" % (
                        MESHER, shlex.quote(raw), shlex.quote(tf_path),
                        " ".join(shlex.quote(b) for b in brps))
                    r = sh(cmd)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            else:
                r = sh("node %s -o %s %s" % (MESHER, shlex.quote(raw), shlex.quote(full)))
            if r.returncode != 0 or not os.path.exists(raw):
                raise RuntimeError((r.stderr or r.stdout).strip()[:200])
            stat = (r.stdout or "").strip().splitlines()[-1]
            with open(raw, "rb") as fin, gzip.open(gz_path, "wb", compresslevel=6) as fout:
                shutil.copyfileobj(fin, fout, 1024 * 1024)
            os.remove(raw)
            gz_size = os.path.getsize(gz_path)
            size = gz_size / 1048576.0

            # 解析"成功"但没有几何：空装配体、只有曲面/线框、或 OCCT 导出为空。
            # 这种产物对用户毫无价值（打开是空场景），而且以前会被当成功、
            # 每轮重解析一遍。现在判 empty 进冷却，并把无用产物删掉。
            if gz_size < MIN_MESH_BYTES:
                try:
                    os.remove(gz_path)
                except OSError:
                    pass
                # 整份三角化为空，先试"拆件 + 按装配位姿拼回"：装配实体存在时
                # 这个构建会整体返回空，拆到零件级则正常（见 assemble_step 注释）。
                if ext in (".step", ".stp"):
                    tmp = tempfile.mkdtemp(prefix="asm_")
                    try:
                        ap = assemble_step(full, tmp, fk)
                        if ap:
                            with open(ap, "rb") as fin, \
                                    gzip.open(gz_path, "wb", compresslevel=6) as fout:
                                shutil.copyfileobj(fin, fout, 1024 * 1024)
                            gz_size = os.path.getsize(gz_path)
                            if gz_size >= MIN_MESH_BYTES:
                                print("%s 整份三角化为空 → 拆件拼装成功 %.1f MB %s"
                                      % (tag, gz_size / 1048576.0, fn), flush=True)
                                ok += 1
                                assembled += 1
                                continue
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                # 再试线框：模型没有实体面，但边通常还在（STEP 文本里就有）。
                # 192MB 的装配体提取 10 万条线段只要 2 秒，比"什么都不给、
                # 用户对着空白页"强得多。有线框就不写空模型标记，用户打开走线框模式。
                try:
                    wpath, wsize, wst = step_wire.build_from_file(full, fk)
                except Exception as exc:
                    print("%s 线框提取失败: %s" % (tag, str(exc)[:80]), flush=True)
                    wpath = ""
                if wpath:
                    # 记 empty 只是为了下一轮不再花 5 分钟重跑三角化；
                    # 不写 .stepmesh.empty 文件，否则用户会被导到"没有实体面"的结论页
                    try:
                        prestate.note_fail(fk, "empty",
                                           "无实体面，已生成线框 %d 条" % wst["segs"], fp)
                    except Exception:
                        pass
                    print("%s 无实体面 → 线框 %d 条线段 %.1f MB %s"
                          % (tag, wst["segs"], wsize / 1048576.0, fn), flush=True)
                    wired += 1
                    continue
                # 留个标记给网关：用户点开这个文件时直接给结论，
                # 不再放行到 O3DV 白解析几十秒（那边同样画不出东西）。
                try:
                    with open(os.path.join(SHEETS, fk + ".stepmesh.empty"), "w",
                              encoding="utf-8") as fh:
                        fh.write("预解析结果为空：模型里没有实体面"
                                 "（只有曲线、曲面或基准面）")
                except OSError:
                    pass
                st, loud = prestate.note_fail(
                    fk, "empty", "网格为空（%d 字节）" % gz_size, fp)
                empty += 1
                if loud:
                    print("%s 无实体网格（第 %d 次）：模型里没有可显示的实体面，"
                          "跳过 %s" % (tag, st["n"],
                                    time.strftime("%m-%d %H:%M",
                                                  time.localtime(st["until"]))),
                          flush=True)
                continue

            redis("set", "kk:srcfp:" + fk, json.dumps(fp), "EX", str(86400 * 30))
            redis("set", "kk:ready:" + fk, "pdf", "EX", "604800")
            mysql("DELETE FROM preview_task WHERE file_key='%s'" % fk)
            mysql("INSERT INTO preview_task (task_id, app_id, emp_no, file_key, file_url,"
                  " file_name, status, ready_at) VALUES ('%s','_all','PRECONV','%s','%s',"
                  " '%s','ready',NOW())"
                  % (hashlib.md5(fk.encode()).hexdigest(), fk,
                     biz_url.replace("'", "''"), fn.replace("'", "''")[:400]))
            # 之前判过空、现在有网格了（源文件换过），把标记清掉
            try:
                os.remove(os.path.join(SHEETS, fk + ".stepmesh.empty"))
            except OSError:
                pass
            prestate.note_ok(fk)
            ok += 1
            print("%s 完成 %.1fs  网格 %.2f MB(gz)  %s"
                  % (tag, time.time() - t0, size, stat[:80]), flush=True)
        except Exception as exc:
            fail += 1
            if os.path.exists(raw):
                try:
                    os.remove(raw)
                except OSError:
                    pass
            st, loud = prestate.note_fail(fk, prestate.kind_of(exc), exc, fp)
            if loud:
                print("%s 失败(第 %d 次，%s): %s  → 下次 %s 之后再试"
                      % (tag, st["n"],
                         prestate.KIND_CN.get(st["kind"], st["kind"]),
                         str(exc)[:140],
                         time.strftime("%m-%d %H:%M", time.localtime(st["until"]))),
                      flush=True)

    print("\n完成 %d / 跳过 %d / 冷却 %d / 加密 %d / 空网格 %d / 转线框 %d"
          " / 拆件拼装 %d / 失败 %d  总 %.1f 分钟"
          % (ok, skip, cool, enc, empty, wired, assembled, fail,
             (time.time() - t_all) / 60))
    if cool or empty:
        print("冷却中的文件不重复解析（源文件变化会自动解除）。"
              "查看: python3 tools/prestate.py --list")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
