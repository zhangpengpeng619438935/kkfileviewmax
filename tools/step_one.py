#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对单个 STEP/IGES 文件生成 3D 网格（或判定"没有实体面"并打标记）。

为什么需要这个工具：预解析（preconvert_step.py）只扫
`/cephfs_data/.../文档中心/受控文件`，而实际被打开的 STEP 还散落在
"研发部"等目录里 —— 这些文件永远不会被预处理，用户每次点开都要
在浏览器里现场解析（192MB 的 STEP 实测 5 分钟以上，页面一直转圈）。

给单个文件补网格比扩大扫描范围现实得多：192MB 的文件解析要 324 秒，
全量扫一遍代价太大，按需处理才划算。

用法（127.0.0.1 宿主机）:
    python3 tools/step_one.py --name "X6M 兼容L460标定图"   # 按文件名模糊找
    python3 tools/step_one.py --fk b7ea6987e1197e...        # 按 fileKey
    python3 tools/step_one.py --url "http://127.0.0.1:8090/files/..." --fk <key>
    python3 tools/step_one.py --name "xxx" --dry-run        # 只看会做什么

结果落到 gateway/sheets：
    <fk>.stepmesh.gz     有实体 → 用户打开走 /stepview，1~3 秒
    <fk>.stepmesh.empty  没实体 → 打开直接给结论，不再白解析
"""
import argparse
import gzip
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prestate  # noqa: E402
import step_wire  # noqa: E402

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
MESHER = "/opt/kkfileview-service/tools/stepmesh/step2mesh.js"
MIN_MESH_BYTES = 2048


def mysql(sql):
    p = subprocess.run(
        ["docker", "exec", "kk-mysql", "mysql", "-ukkfileview", "-pchange-me-db-password",
         "kkfileview", "--default-character-set=utf8mb4", "-N", "-B", "-e", sql],
        capture_output=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit((p.stderr or b"").decode("utf-8", "replace")[:300])
    return (p.stdout or b"").decode("utf-8", "replace")


def lookup(name="", fk=""):
    """从库里找 (file_key, file_url, file_name)。"""
    if fk:
        cond = "file_key='%s'" % fk
    else:
        cond = "file_name LIKE '%%%s%%'" % name
    sql = ("SELECT file_key, file_url, file_name FROM preview_task WHERE %s"
           " ORDER BY created_at DESC LIMIT 1" % cond)
    out = mysql(sql).strip()
    if not out:
        sql = ("SELECT file_key, file_url, file_name FROM audit_log WHERE %s"
               " ORDER BY created_at DESC LIMIT 1" % cond)
        out = mysql(sql).strip()
    if not out:
        return None
    p = out.split("\t")
    return {"fk": p[0], "url": p[1], "name": p[2] if len(p) > 2 else ""}


def mesh_via_python(src, out_mesh):
    """用原生 OCCT 兜底三角化（tools/stepmesh_py.py）。

    为什么需要：occt-import-js 是浏览器用的 WASM 构建，个别 STEP 它会
    "ReadStepFile 成功但 meshes=0"（实测 427CSUV-2025.stp：61.8MB / 40 个实体，
    内存峰值才 706MB，不是 OOM）。同一份文件用原生 OCCT 7.9 读：44 solid /
    23875 face，三角化 3.3 秒出 34 万面片。
    返回 True 表示拿到了网格。
    """
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stepmesh_py.py")
    if not os.path.exists(script):
        return False
    try:
        r = subprocess.run([sys.executable, script, "-o", out_mesh, src],
                           capture_output=True, timeout=1800)
    except Exception as exc:
        print("  原生 OCCT 三角化异常: %s" % str(exc)[:100])
        return False
    tail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    if tail:
        print("  " + tail[-1][:150])
    if r.returncode != 0 or not os.path.exists(out_mesh) or os.path.getsize(out_mesh) < 2048:
        return False
    return True


def write_gz_atomic(src_path, gz_path):
    """量化 + 先写 .part 再 rename。

    直接 gzip.open(gz_path, "wb") 有两个坑：
    ① 谁在这期间打开预览，gzip 流是断的，解出来的顶点数组缺尾巴，
       前端算出 NaN 包围盒，页面直接报"模型载入失败"（实测踩到过一次）；
    ② 中途失败会留下半截文件，之后所有打开都失败，还得人工删。

    量化（meshpack）：u16 索引 + int16 坐标 + int8 法线。实测 86.5MB 的网格
    压成 36.8MB，gzip 后 35.7MB → 15.8MB（-56%），耗时 0.3 秒。
    失败不影响出图：捕获异常后退回未量化的原始数据。
    KK_PACK_MESH=0 可整体关掉。
    """
    packed = None
    if os.environ.get("KK_PACK_MESH", "1") != "0":
        try:
            import meshpack
            packed = gz_path + ".pack"
            r = meshpack.pack_file(src_path, packed)
            if r:
                print("  网格量化 %.1f → %.1f MB" % (r[0] / 1048576.0, r[1] / 1048576.0))
                src_path = packed
            else:
                packed = None
        except Exception as exc:
            print("  网格量化失败（用原始数据）: %s" % str(exc)[:100])
            packed = None
    part = gz_path + ".part"
    try:
        with open(src_path, "rb") as fin, gzip.open(part, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout, 1024 * 1024)
        os.replace(part, gz_path)
    finally:
        if packed:
            try:
                os.remove(packed)
            except OSError:
                pass


def has_assembly(path):
    """文件里有没有 NEXT_ASSEMBLY_USAGE_OCCURRENCE（装配实例）。

    有 NAUO 的 STEP，occt-import-js 整份三角化必然返回空 —— 实测 81.5MB 的台架
    白跑 364 秒才拿回一个空网格，然后才走拆件。提前扫一遍（1 秒）能把这 6 分钟省掉。
    """
    pat = b"NEXT_ASSEMBLY_USAGE_OCCURRENCE"
    tail = b""
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                return False
            if pat in tail + chunk:
                return True
            tail = chunk[-(len(pat) - 1):]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fk", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--url", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    hit = lookup(a.name, a.fk)
    if not hit:
        raise SystemExit("库里找不到这个文件（可以用 --url + --fk 直接指定）")
    fk = a.fk or hit["fk"]
    url = a.url or hit["url"]
    print("文件: %s" % (hit["name"] or url)[:70])
    print("fk  : %s" % fk)
    gz = os.path.join(SHEETS, fk + ".stepmesh.gz")
    empty = os.path.join(SHEETS, fk + ".stepmesh.empty")
    if os.path.getsize(gz) > MIN_MESH_BYTES if os.path.exists(gz) else False:
        print("已有网格 %d KB，无需重做（--force 可重跑，当前未实现）"
              % (os.path.getsize(gz) // 1024))
        return 0

    if a.dry_run:
        print("dry-run：会下载 %s 并解析" % url[:80])
        return 0

    tmp = tempfile.mkdtemp(prefix="step1_")
    src = os.path.join(tmp, "in.step")
    out = os.path.join(tmp, "out.mesh")
    try:
        t0 = time.time()
        print("下载中…")
        urllib.request.urlretrieve(url, src)
        size_mb = os.path.getsize(src) / 1048576.0
        print("源文件 %.1f MB" % size_mb)
        # 装配体（含 NAUO）整份三角化必然为空，先花 1 秒扫一眼，能省掉几分钟白跑
        skip_whole = (src.lower().endswith((".step", ".stp")) and size_mb > 8
                      and has_assembly(src))
        if skip_whole:
            print("检测到装配实例（NAUO）：跳过整份三角化，直接拆件")
            gz_size = 0
        else:
            print("解析中（大文件要几分钟）…")
            r = subprocess.run(["node", MESHER, "-o", out, src],
                               capture_output=True, timeout=3600)
            detail = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
            if detail:
                print("  " + detail[-1][:150])
            cost = time.time() - t0
            if r.returncode != 0 or not os.path.exists(out):
                raise RuntimeError((r.stderr or r.stdout).decode("utf-8", "replace")[:200])

            os.makedirs(SHEETS, exist_ok=True)
            write_gz_atomic(out, gz)
            gz_size = os.path.getsize(gz)
        if gz_size < MIN_MESH_BYTES:
            # 装配实体存在时整份三角化会全空，先试"拆件 + 按位姿拼回"
            if src.lower().endswith((".step", ".stp")):
                try:
                    import preconvert_step as pcs
                    tmp = tempfile.mkdtemp(prefix="asm_")
                    try:
                        ap = pcs.assemble_step(src, tmp, fk)
                        if ap:
                            # 必须 gzip：产物后缀是 .stepmesh.gz，网关按 gzip 提供，
                            # 直接 copyfile 会写出 46MB 未压缩数据，浏览器解不开。
                            write_gz_atomic(ap, gz)
                            gz_size = os.path.getsize(gz)
                            print("结果: 整份三角化为空 → 拆件拼装成功 %.1f MB"
                                  % (gz_size / 1048576.0))
                            return 0
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                except Exception as exc:
                    print("拆件拼装失败: %s" % str(exc)[:120])

                # 拆件也没结果（比如文件里没有 NAUO、只有一个大毛坯）：
                # 换原生 OCCT 再试一次，WASM 版读不动的文件它能读
                if src.lower().endswith((".step", ".stp", ".p21")):
                    py_mesh = os.path.join(os.path.dirname(out), "py.mesh")
                    print("  改用原生 OCCT 三角化…")
                    if mesh_via_python(src, py_mesh):
                        write_gz_atomic(py_mesh, gz)
                        gz_size = os.path.getsize(gz)
                        if gz_size >= MIN_MESH_BYTES:
                            print("结果: WASM 版无网格 → 原生 OCCT 成功 %.1f MB"
                                  % (gz_size / 1048576.0))
                            return 0
            # 没有实体面：先试线框（从 STEP 文本取边，几秒钟的事），
            # 线框也没有才承认这是"空模型"
            os.remove(gz)
            wpath, wsize, wst = step_wire.build_from_file(src, fk)
            if wpath:
                # 留个标记：转换已跑完但确实没有实体面。
                # 前端据此把"实体网格解析中…"换成"该文件没有实体面"，
                # 否则用户会以为还在转、对着线框一直等。
                try:
                    with open(os.path.join(SHEETS, fk + ".stepmesh.nomesh"), "w",
                              encoding="utf-8") as fh:
                        fh.write("该文件没有可三角化的实体面，仅显示线框")
                except OSError:
                    pass
                print("结果: 无实体面，已生成线框 %d 条线段 / %.1f MB"
                      "（网格 %.0f 秒，线框 %.1f 秒）—— 打开走线框模式"
                      % (wst["segs"], wsize / 1048576.0, cost, wst["scan"]))
                return 0
            with open(empty, "w", encoding="utf-8") as fh:
                fh.write("预解析结果为空：模型里没有实体面，也没有可显示的边")
            try:
                prestate.note_fail(fk, "empty", "网格为空（%d 字节）" % gz_size, "")
            except Exception:
                pass
            print("结果: 无实体面且无线框（%.0f 秒），已写 .stepmesh.empty" % cost)
        else:
            print("结果: 网格 %.1f MB（%.0f 秒）—— 用户打开走 /stepview，1~3 秒"
                  % (gz_size / 1048576.0, cost))
            for _p in (empty, os.path.join(SHEETS, fk + ".stepmesh.nomesh")):
                try:
                    os.remove(_p)
                except OSError:
                    pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
