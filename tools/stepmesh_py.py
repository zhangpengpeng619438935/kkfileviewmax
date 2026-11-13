#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用原生 OCCT（cadquery-ocp）把 STEP 三角化，产出与 step2mesh.js 相同的 S3DM。

为什么需要它：occt-import-js 是**浏览器用的 WASM 构建**，跑 427CSUV-2025.stp
（61.8MB / 92 万实体 / 40 个实体）时 ReadStepFile 返回 success 却 **meshes=0**，
内存也没爆（峰值 706MB）。同一份文件用原生 OCCT 7.9 读：44 solid / 23875 face，
21 秒读完。所以不是文件的问题，是那个 WASM 构建的转换器不够用。

用法：
    python3 stepmesh_py.py -o out.mesh in.step [in2.step ...]
"""
import argparse
import array
import json
import os
import struct
import sys
import time

from OCP.STEPControl import STEPControl_Reader
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopoDS import TopoDS


def collect(shape, kind):
    out = []
    ex = TopExp_Explorer(shape, kind)
    while ex.More():
        out.append(TopoDS.Solid_s(ex.Current()) if kind == TopAbs_SOLID
                   else TopoDS.Face_s(ex.Current()))
        ex.Next()
    return out


def mesh_shape(shape, deflection=0.1, angular=0.5):
    """返回 [(positions, indices, normals)]，按 SOLID 分组（没有 solid 就整体一组）。"""
    t0 = time.time()
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    groups = collect(shape, TopAbs_SOLID)
    if not groups:
        groups = [shape]
    meshes = []
    for g in groups:
        faces = collect(g, TopAbs_FACE)
        pos = array.array("f")
        nrm = array.array("f")
        idx = array.array("I")
        base = 0
        for f in faces:
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(f, loc)
            if tri is None or tri.NbNodes() < 3:
                continue
            trsf = loc.Transformation() if not loc.IsIdentity() else None
            n = tri.NbNodes()
            pts = []
            for i in range(1, n + 1):
                p = tri.Node(i)
                if trsf is not None:
                    p = p.Transformed(trsf)
                pts.append((p.X(), p.Y(), p.Z()))
            rev = (f.Orientation() == TopAbs_REVERSED)
            nt = tri.NbTriangles()
            tris = []
            for i in range(1, nt + 1):
                t = tri.Triangle(i)
                a, b, c = t.Get()
                tris.append((a, b, c) if not rev else (a, c, b))
            if not tris:
                continue
            # 顶点法线：把相邻三角面的法线累加到顶点上（同一面内平滑，
            # 面与面之间天然断开——机械件要的就是这种硬边效果）
            acc = [[0.0, 0.0, 0.0] for _ in range(n)]
            for (a, b, c) in tris:
                pa, pb, pc = pts[a - 1], pts[b - 1], pts[c - 1]
                ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
                vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
                nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
                ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
                nx, ny, nz = nx / ln, ny / ln, nz / ln
                for k in (a, b, c):
                    q = acc[k - 1]
                    q[0] += nx; q[1] += ny; q[2] += nz
            for i, p in enumerate(pts):
                pos.extend(p)
                q = acc[i]
                ln = (q[0] * q[0] + q[1] * q[1] + q[2] * q[2]) ** 0.5 or 1.0
                nrm.extend((q[0] / ln, q[1] / ln, q[2] / ln))
            for (a, b, c) in tris:
                idx.extend((base + a - 1, base + b - 1, base + c - 1))
            base += n
        if pos:
            meshes.append((pos, idx, nrm))
    print("  三角化 %.1f 秒，%d 组 / %d 面片"
          % (time.time() - t0, len(meshes), sum(len(m[1]) // 3 for m in meshes)))
    return meshes


def write_s3dm(path, meshes, names=None):
    chunks, items, off = [], [], 0
    for i, (pos, idx, nrm) in enumerate(meshes):
        pos_b = pos.tobytes()
        it = {"name": (names[i] if names and i < len(names) else "solid_%d" % (i + 1)),
              "vertices": len(pos) // 3,
              "posOffset": off, "posBytes": len(pos_b),
              "idxOffset": -1, "idxBytes": 0, "nrmOffset": -1, "nrmBytes": 0,
              "color": None}
        chunks.append(pos_b)
        off += len(pos_b)
        if idx:
            idx_b = idx.tobytes()
            it["idxOffset"] = off
            it["idxBytes"] = len(idx_b)
            chunks.append(idx_b)
            off += len(idx_b)
        if nrm:
            nrm_b = nrm.tobytes()
            it["nrmOffset"] = off
            it["nrmBytes"] = len(nrm_b)
            chunks.append(nrm_b)
            off += len(nrm_b)
        items.append(it)
    meta = json.dumps({"meshes": items}, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as f:
        f.write(b"S3DM" + struct.pack("<I", len(meta)) + meta + b"".join(chunks))
    return sum(i["vertices"] for i in items)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="+")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--deflection", type=float, default=0.1)
    ap.add_argument("--angular", type=float, default=0.5)
    a = ap.parse_args()

    all_meshes = []
    for src in a.src:
        r = STEPControl_Reader()
        st = r.ReadFile(src)
        if st != IFSelect_RetDone:
            print("  读取失败: %s" % src, file=sys.stderr)
            continue
        r.TransferRoots()
        sh = r.OneShape()
        if sh.IsNull():
            print("  空形状: %s" % src, file=sys.stderr)
            continue
        all_meshes.extend(mesh_shape(sh, a.deflection, a.angular))

    if not all_meshes:
        print("没有生成任何网格", file=sys.stderr)
        return 1
    verts = write_s3dm(a.out, all_meshes)
    print("写出 %s  %.1f MB  顶点 %d" % (a.out, os.path.getsize(a.out) / 1048576, verts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
