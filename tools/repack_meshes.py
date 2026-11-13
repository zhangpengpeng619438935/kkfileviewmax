#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已存在的 .stepmesh.gz 就地重打包成量化格式（不用重新转换）。

为什么要单独跑一次：量化是后加的，之前转好的产物还是 float32/u32，
一个 86MB 的装配体 gzip 后 35MB。重打包不碰 STEP、不跑三角化，
每份 0.3 秒左右，直接省掉一半体积和一半下载时间。

用法：
    python3 tools/repack_meshes.py --dry            # 看看能省多少
    python3 tools/repack_meshes.py --limit 5        # 先试 5 个
    python3 tools/repack_meshes.py                  # 全量
"""
import argparse
import glob
import gzip
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import meshpack

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"


def is_quantized(raw):
    try:
        n = struct.unpack("<I", raw[4:8])[0]
        meta = json.loads(raw[8:8 + n])
    except Exception:
        return True          # 读不出 meta 的直接跳过，别动它
    return bool(meta.get("quant"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（试跑用）")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(SHEETS, "*.stepmesh.gz")))
    before = after = 0
    done = skip = fail = 0
    t0 = time.time()

    for i, f in enumerate(files):
        if a.limit and done >= a.limit:
            break
        try:
            with gzip.open(f, "rb") as fh:
                raw = fh.read()
        except Exception as exc:
            print("  跳过（读不出）  %s  %s" % (os.path.basename(f)[:12], str(exc)[:60]))
            fail += 1
            continue
        if is_quantized(raw):
            skip += 1
            continue

        out = meshpack.pack_bytes(raw)
        if out is None:
            skip += 1
            continue
        gz = gzip.compress(out, 6)
        old = os.path.getsize(f)
        before += old
        after += len(gz)
        done += 1
        if a.dry:
            print("  %-12s %6.1fMB → %5.1fMB" % (os.path.basename(f)[:12], old / 1048576,
                                                len(gz) / 1048576))
            continue
        part = f + ".part"
        with open(part, "wb") as fh:
            fh.write(gz)
        os.replace(part, f)          # 原子替换，正在预览的人不会读到半截
        if done % 50 == 0:
            print("  已处理 %d 个，%.0f → %.0f MB（%.0f 秒）"
                  % (done, before / 1048576, after / 1048576, time.time() - t0))

    print("共 %d 个文件：处理 %d，已是量化格式 %d，失败 %d"
          % (len(files), done, skip, fail))
    if done:
        print("体积 %.0f MB → %.0f MB（省 %.0f MB，%.0f%%），耗时 %.0f 秒"
              % (before / 1048576, after / 1048576,
                 (before - after) / 1048576,
                 (before - after) * 100.0 / max(before, 1), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
