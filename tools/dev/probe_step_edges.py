#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证：能否直接从 STEP 文本里提取"边"来画线框（绕开 OCCT 的 5 分钟解析）。

为什么走这条路：occt-import-js 只输出三角网格，对这份 192MB / 281 个实体的
巨型装配体，解析 324 秒后 meshes 仍为 0（多半是没跑完或某处失败）。
而 STEP 是纯文本（ISO 10303-21），边的端点信息就写在里面：

    #12 = EDGE_CURVE('', #10, #11, #curve, .T.);   ← 两个 VERTEX_POINT
    #10 = VERTEX_POINT('', #5);                    ← 指向 CARTESIAN_POINT
    #5  = CARTESIAN_POINT('', (x,y,z));

所以不依赖任何 CAD 库也能拿到"线段集合"，体积小、浏览器画 LineSegments 很轻。

用法: python3 probe_step_edges.py <file.step> [--dump out.bin]
输出: 点数 / 边数 / 唯一线段数 / 用时 / 估算产物大小
"""
import array
import re
import struct
import sys
import time

PAT_PT = re.compile(r"^#(\d+)\s*=\s*CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*"
                    r"\(\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)")
PAT_VP = re.compile(r"^#(\d+)\s*=\s*VERTEX_POINT\s*\(\s*'[^']*'\s*,\s*#(\d+)")
PAT_EC = re.compile(r"^#(\d+)\s*=\s*EDGE_CURVE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*#(\d+)")


def main():
    path = sys.argv[1]
    dump = ""
    if "--dump" in sys.argv:
        dump = sys.argv[sys.argv.index("--dump") + 1]

    pts, vps, edges = {}, {}, []
    t0 = time.time()
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            n += 1
            # 注意实际文件里是 "CARTESIAN_POINT ("（括号前有空格），
            # 用带括号的串判断会一条也匹配不到
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

    # 边 → 两个端点坐标
    seg = array.array("f")
    used = set()
    for v1, v2 in edges:
        p1 = pts.get(vps.get(v1, ""))
        p2 = pts.get(vps.get(v2, ""))
        if p1 and p2:
            seg.extend(p1 + p2)
            used.add(vps[v1])
            used.add(vps[v2])

    nseg = len(seg) // 6
    size = nseg * 6 * 4                      # 两个端点 × xyz × float32
    print("扫描 %d 行 / %.1f 秒" % (n, scan))
    print("点表 %d 个（被边引用 %d 个）" % (len(pts), len(used)))
    print("边 %d 条 → 可用线段 %d 条（%.1f MB 原始数据）"
          % (len(edges), nseg, size / 1048576.0))
    if nseg:
        xs = seg[0::3]
        ys = seg[1::3]
        zs = seg[2::3]
        print("包围盒 X[%.1f, %.1f] Y[%.1f, %.1f] Z[%.1f, %.1f]"
              % (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)))

    if dump and nseg:
        # W3DM: magic + float32 线段数组（每 6 个数一条线段）
        with open(dump, "wb") as fh:
            fh.write(b"W3DM")
            fh.write(struct.pack("<I", nseg))
            fh.write(seg.tobytes())
        import os
        print("已写出 %s（%.1f MB）" % (dump, os.path.getsize(dump) / 1048576.0))


if __name__ == "__main__":
    main()
