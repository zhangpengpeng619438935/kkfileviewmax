#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 STEP 文本里提取线框（EDGE_CURVE 端点），落成 <fk>.stepwire.gz。

**为什么需要这条通道**

OCCT 三角化（tools/stepmesh/step2mesh.js）对两类模型无能为力：
  ① 模型里只有曲线/边，没有实体面 → meshes 0；
  ② 装配体太大 → 解析几分钟仍未完成（实测 192MB / 281 个实体，
     parse 324 秒后 meshes 仍是 0）。
这两种情况以前只能标"空模型"，用户看到的是一句说明。

其实边信息就写在 STEP 文本里：

    #12 = EDGE_CURVE ( '', #10, #11, #curve, .T. ) ;   ← 两个 VERTEX_POINT
    #10 = VERTEX_POINT ( '', #5 ) ;                    ← 指向 CARTESIAN_POINT
    #5  = CARTESIAN_POINT ( '', ( x, y, z ) ) ;

192MB 的文件扫一遍只要 2.3 秒，10.7 万条线段 2.4MB（gzip 后约 1MB），
浏览器画 LineSegments 毫无压力——比 O3DV 现场解析快两个数量级。

产物格式（W3DM）:
    4 字节 magic "W3DM" + uint32 线段数 + float32 端点数组（每条线段 6 个数）

用法（127.0.0.1 宿主机）:
    python3 tools/step_wire.py --name "X6M 兼容L460标定图"
    python3 tools/step_wire.py --fk b7ea6987…
    python3 tools/step_wire.py --url "http://…" --fk <key>
"""
import argparse
import array
import gzip
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
MIN_WIRE_BYTES = 512          # 低于这个值当作"连边都没有"

PAT_PT = re.compile(r"^#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*"
                    r"\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)")
PAT_VP = re.compile(r"^#(\d+)\s*=\s*VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)")
PAT_EC = re.compile(r"^#(\d+)\s*=\s*EDGE_CURVE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)")


def mysql(sql):
    p = subprocess.run(
        ["docker", "exec", "kk-mysql", "mysql", "-ukkfileview", "-pchange-me-db-password",
         "kkfileview", "--default-character-set=utf8mb4", "-N", "-B", "-e", sql],
        capture_output=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit((p.stderr or b"").decode("utf-8", "replace")[:300])
    return (p.stdout or b"").decode("utf-8", "replace")


def lookup(name="", fk=""):
    cond = "file_key='%s'" % fk if fk else "file_name LIKE '%%%s%%'" % name
    for table in ("preview_task", "audit_log"):
        out = mysql("SELECT file_key, file_url, file_name FROM %s WHERE %s"
                    " ORDER BY created_at DESC LIMIT 1" % (table, cond)).strip()
        if out:
            p = out.split("\t")
            return {"fk": p[0], "url": p[1], "name": p[2] if len(p) > 2 else ""}
    return None


def extract(path):
    """扫 STEP 文本，返回 (array 线段顶点, 统计 dict)。"""
    pts, vps, edges = {}, {}, []
    t0 = time.time()
    lines = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            lines += 1
            # 实际文件里是 "CARTESIAN_POINT ("（括号前有空格），
            # 用带括号的串判断会一条都匹配不到
            if "CARTESIAN_POINT" in line:
                m = PAT_PT.match(line)
                if m:
                    pts[m.group(1)] = (float(m.group(2)), float(m.group(3)),
                                       float(m.group(4)))
            elif "VERTEX_POINT" in line:
                m = PAT_VP.match(line)
                if m:
                    vps[m.group(1)] = m.group(2)
            elif "EDGE_CURVE" in line:
                m = PAT_EC.match(line)
                if m:
                    edges.append((m.group(2), m.group(3)))
    scan = time.time() - t0
    seg = array.array("f")
    for v1, v2 in edges:
        p1 = pts.get(vps.get(v1, ""))
        p2 = pts.get(vps.get(v2, ""))
        if p1 and p2:
            seg.extend(p1 + p2)
    return seg, {"lines": lines, "scan": scan, "points": len(pts),
                 "edges": len(edges), "segs": len(seg) // 6}


PAT_XYZ = re.compile(
    r"CARTESIAN_POINT\s*\([^,]*,\s*\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)")


def has_assembly(path):
    """有没有 NEXT_ASSEMBLY_USAGE_OCCURRENCE（装配实例）。"""
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


def extract_assembly(path):
    """带装配实例的线框：每个实例按自己的位姿各复制一份边。

    纯文本扫描（extract）只看得到"零件定义里有哪条边"，看不到"这个零件摆在哪、
    摆了几份"。装配体这样画出来就只有零件库那一份，实测 1129 个实例只出 6.2 万条边，
    看着是残缺的。这里改用实体图：

        PRODUCT_DEFINITION → 几何表示 → 闭包里的 EDGE_CURVE → 端点
        NAUO 链累乘出每个实例的矩阵 → 作用到端点上

    代价是慢（要建整张实体表），81MB 约 40~60 秒；换来的是完整轮廓。
    """
    import step_split as sp
    from step_assemble import Assembly, apply as xf

    t0 = time.time()
    header, ents = sp.load(path)
    asm = Assembly(header, ents)
    occ = asm.occurrences()
    roots_of = dict(asm.model.part_roots)
    load_sec = time.time() - t0

    cache = {}

    def point_of(vid):
        vb = ents.get(vid, "")
        if vb.split("(", 1)[0].strip() != "VERTEX_POINT":
            return None
        vr = sp.refs(vb)
        if not vr:
            return None
        m = PAT_XYZ.search(ents.get(vr[0], ""))
        if not m:
            return None
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))

    def edges_of(geo):
        if geo in cache:
            return cache[geo]
        out = []
        roots = roots_of.get(geo)
        if roots:
            for eid in sp.closure(ents, roots):
                b = ents.get(eid, "")
                if b.split("(", 1)[0].strip() != "EDGE_CURVE":
                    continue
                rr = sp.refs(b)
                if len(rr) < 2:
                    continue
                p1, p2 = point_of(rr[0]), point_of(rr[1])
                if p1 and p2:
                    out.append((p1, p2))
        cache[geo] = out
        return out

    seg = array.array("f")
    for geo, mat, _name in occ:
        for p1, p2 in edges_of(geo):
            a = xf(mat, list(p1))
            b = xf(mat, list(p2))
            seg.extend((a[0], a[1], a[2], b[0], b[1], b[2]))
    return seg, {"lines": 0, "scan": load_sec, "points": len(cache),
                 "edges": len(seg) // 6, "segs": len(seg) // 6, "assembly": 1}


def write_wire(seg, fk):
    os.makedirs(SHEETS, exist_ok=True)
    out = os.path.join(SHEETS, fk + ".stepwire.gz")
    # 先写 .part 再 replace：边转边被打开时，读到的永远是完整的 gzip
    part = out + ".part"
    n = len(seg) // 6
    with gzip.open(part, "wb", compresslevel=6) as fh:
        fh.write(b"W3DM")
        fh.write(struct.pack("<I", n))
        fh.write(seg.tobytes())
    os.replace(part, out)
    return out, os.path.getsize(out)


def build_from_file(step_path, fk):
    """供 step_one.py 复用：从已下载的 STEP 生成线框，返回 (path, size, stat)。

    装配体（有 NAUO）走实体图 + 实例变换，能画出完整的 1129 份实例；
    普通零件仍走纯文本扫描（2 秒级）。
    """
    if step_path.lower().endswith((".step", ".stp")) and has_assembly(step_path):
        seg, stat = extract_assembly(step_path)
    else:
        seg, stat = extract(step_path)
    if len(seg) // 6 == 0:
        return "", 0, stat
    out, size = write_wire(seg, fk)
    if size < MIN_WIRE_BYTES:
        os.remove(out)
        return "", 0, stat
    # 有线框了，就不再需要"没有实体面"的结论标记
    try:
        os.remove(os.path.join(SHEETS, fk + ".stepmesh.empty"))
    except OSError:
        pass
    return out, size, stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fk", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--url", default="")
    a = ap.parse_args()
    hit = lookup(a.name, a.fk)
    if not hit:
        raise SystemExit("库里找不到这个文件（可用 --url + --fk 直接指定）")
    fk = a.fk or hit["fk"]
    url = a.url or hit["url"]
    print("文件: %s" % (hit["name"] or url)[:70])

    tmp = tempfile.mkdtemp(prefix="stepwire_")
    src = os.path.join(tmp, "in.step")
    try:
        print("下载中…")
        urllib.request.urlretrieve(url, src)
        print("源文件 %.1f MB" % (os.path.getsize(src) / 1048576.0))
        print("提取线框…")
        out, size, st = build_from_file(src, fk)
        print("扫描 %d 行 / %.1f 秒；点 %d、边 %d"
              % (st["lines"], st["scan"], st["points"], st["edges"]))
        if not out:
            print("结果: 连边都没有（%d 条）" % st["segs"])
            return 1
        print("结果: %d 条线段 → %s（%.1f MB）"
              % (st["segs"], os.path.basename(out), size / 1048576.0))
        print("用户点开将走 /stepview 的线框模式")
        return 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
