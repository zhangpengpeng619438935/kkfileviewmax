#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S3DM 网格压缩：u16 索引 + int16 坐标 + int8 法线。

为什么值得做（实测 1129 实例的 X6M 台架，未压缩 86.2MB）：
    indices   33.5 MB   → u16 后 16.8 MB（**无损**：所有网格顶点都 < 65536）
    positions 26.4 MB   → int16 后 13.2 MB（精度 = 包围盒最大边 / 65535，约 0.03mm）
    normals   26.4 MB   → int8 后 6.6 MB（±1/127，机械件看不出差别，解码时归一化）

格式保持 S3DM 不变，只在 meta 里加描述字段，老文件（没有这些字段）按 float32/u32 读，
前端两种都支持。这样存量和新增产物可以共存，不需要批量重转。

用法：
    python3 tools/meshpack.py in.mesh out.mesh     # 单独压一份看体积
"""
import json
import os
import struct
import sys

import numpy as np

MAGIC = b"S3DM"


def _meta(buf):
    if buf[:4] != MAGIC:
        raise ValueError("不是 S3DM")
    n = struct.unpack("<I", buf[4:8])[0]
    return json.loads(buf[8:8 + n]), 8 + n


def pack_bytes(buf):
    """返回压缩后的 S3DM 字节。空网格或没有坐标时原样返回。"""
    meta, base = _meta(buf)
    meshes = meta.get("meshes") or []
    if not meshes:
        return None

    # 1) 全局包围盒（坐标已经是世界坐标，整机共用一个原点/比例就够）
    mins = np.full(3, np.inf, dtype=np.float64)
    maxs = np.full(3, -np.inf, dtype=np.float64)
    for m in meshes:
        n = m.get("posBytes", 0) // 4
        if n < 3:
            continue
        pos = np.frombuffer(buf, dtype="<f4", count=n, offset=base + m["posOffset"])
        pos = pos.reshape(-1, 3).astype(np.float64)
        mins = np.minimum(mins, pos.min(0))
        maxs = np.maximum(maxs, pos.max(0))
    if not np.isfinite(mins).all():
        return None

    extent = float((maxs - mins).max())
    if extent <= 0:
        return None
    scale = 65535.0 / extent          # 保证量化后落在 [0, 65535]

    chunks, items, off = [], [], 0
    for m in meshes:
        it = {"name": m.get("name", ""), "vertices": m.get("vertices", 0), "color": m.get("color")}
        nv = m.get("posBytes", 0) // 4
        if nv >= 3:
            pos = np.frombuffer(buf, dtype="<f4", count=nv, offset=base + m["posOffset"])
            pos = pos.reshape(-1, 3).astype(np.float64)
            q = np.rint((pos - mins) * scale).astype(np.int32) - 32768
            np.clip(q, -32768, 32767, out=q)
            data = q.astype("<i2").tobytes()
            it.update({"posOffset": off, "posBytes": len(data), "posType": "i16"})
            chunks.append(data)
            off += len(data)
        else:
            it.update({"posOffset": -1, "posBytes": 0, "posType": "i16"})

        ib = m.get("idxBytes", 0)
        it.update({"idxOffset": -1, "idxBytes": 0, "idxType": "u32"})
        if ib >= 12:
            idx = np.frombuffer(buf, dtype="<u4", count=ib // 4, offset=base + m["idxOffset"])
            if idx.size and int(idx.max()) < 65536:
                data = idx.astype("<u2").tobytes()
                it.update({"idxOffset": off, "idxBytes": len(data), "idxType": "u16"})
            else:
                data = idx.astype("<u4").tobytes()
                it.update({"idxOffset": off, "idxBytes": len(data), "idxType": "u32"})
            chunks.append(data)
            off += len(data)

        nb = m.get("nrmBytes", 0)
        it.update({"nrmOffset": -1, "nrmBytes": 0, "nrmType": "i8"})
        if nb >= 12:
            nrm = np.frombuffer(buf, dtype="<f4", count=nb // 4, offset=base + m["nrmOffset"])
            nrm = np.clip(nrm.reshape(-1, 3), -1.0, 1.0)
            data = np.rint(nrm * 127.0).astype("<i1").tobytes()
            it.update({"nrmOffset": off, "nrmBytes": len(data), "nrmType": "i8"})
            chunks.append(data)
            off += len(data)

        items.append(it)

    out_meta = {"meshes": items, "quant": True,
                "org": [float(v) for v in mins], "scale": scale}
    mb = json.dumps(out_meta, separators=(",", ":")).encode("utf-8")
    return MAGIC + struct.pack("<I", len(mb)) + mb + b"".join(chunks)


def pack_file(src, dst):
    """压缩一份文件。返回 (原始字节数, 压缩后字节数)；不适用时返回 None。"""
    with open(src, "rb") as f:
        buf = f.read()
    out = pack_bytes(buf)
    if out is None:
        return None
    with open(dst, "wb") as f:
        f.write(out)
    return len(buf), len(out)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    r = pack_file(sys.argv[1], sys.argv[2])
    if not r:
        print("不适用（空网格或坐标缺失）")
        return 1
    a, b = r
    print("原始 %.1f MB → 压缩 %.1f MB（%.0f%%）" % (a / 1048576, b / 1048576, b * 100.0 / a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
