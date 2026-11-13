#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给"只有线框、没有实体"的历史文件补网格：直接用原生 OCCT 重跑，不走 WASM。

适用于 stepmesh_py.py 上线前就已经回退成线框的那些文件——它们的 STEP 本来
就是 WASM 版读不动的，再走一遍 node 也是白等 80 秒。

用法：
    python3 tools/remesh_python.py <fk> [<fk> ...]
    python3 tools/remesh_python.py --all-wire            # 自动找 sheets 下只有线框的
"""
import argparse
import glob
import gzip
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import meshpack

SHEETS = "/opt/kkfileview-service/data/gateway/sheets"
def url_of(fk):
    """宿主机上没有 mysql 客户端，用 pymysql 直连（pip install --user pymysql）。"""
    import pymysql
    c = pymysql.connect(host="127.0.0.1", port=13306, user="kkfileview",
                        password="change-me-db-password", database="kkfileview", charset="utf8mb4")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT file_url FROM audit_log WHERE file_key=%s "
                        "AND file_url<>'' ORDER BY created_at DESC LIMIT 1", (fk,))
            row = cur.fetchone()
            return (row[0] if row else "") or ""
    finally:
        c.close()


def do_one(fk):
    url = url_of(fk)
    if not url:
        print("  %s 找不到源地址" % fk[:12])
        return False
    tmp = tempfile.mkdtemp(prefix="remesh_")
    src = os.path.join(tmp, "in.step")
    mesh = os.path.join(tmp, "out.mesh")
    try:
        t0 = time.time()
        urllib.request.urlretrieve(url, src)
        r = subprocess.run([sys.executable, os.path.join(HERE, "stepmesh_py.py"),
                            "-o", mesh, src], capture_output=True, text=True, timeout=1800)
        tail = (r.stdout or "").strip().splitlines()
        if tail:
            print("    " + tail[-1][:110])
        if r.returncode != 0 or not os.path.exists(mesh) or os.path.getsize(mesh) < 2048:
            print("  %s 原生 OCCT 也没出网格" % fk[:12])
            return False
        # 量化 + gzip + 原子替换
        packed = mesh + ".pack"
        packed_ok = meshpack.pack_file(mesh, packed) is not None
        src_mesh = packed if packed_ok else mesh
        gz = os.path.join(SHEETS, fk + ".stepmesh.gz")
        part = gz + ".part"
        with open(src_mesh, "rb") as fin, gzip.open(part, "wb", compresslevel=6) as fout:
            while True:
                b = fin.read(1 << 20)
                if not b:
                    break
                fout.write(b)
        os.replace(part, gz)
        for p in (os.path.join(SHEETS, fk + ".stepmesh.nomesh"),
                  os.path.join(SHEETS, fk + ".stepmesh.empty")):
            try:
                os.remove(p)
            except OSError:
                pass
        print("  %s ✓ %.1f MB  %.0f 秒" % (fk[:12], os.path.getsize(gz) / 1048576,
                                          time.time() - t0))
        return True
    except Exception as exc:
        print("  %s 失败: %s" % (fk[:12], str(exc)[:100]))
        return False
    finally:
        for p in glob.glob(os.path.join(tmp, "*")):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fks", nargs="*")
    ap.add_argument("--all-wire", action="store_true", help="自动找只有线框没网格的文件")
    a = ap.parse_args()

    fks = list(a.fks)
    if a.all_wire:
        for w in glob.glob(os.path.join(SHEETS, "*.stepwire.gz")):
            b = os.path.basename(w)[:-len(".stepwire.gz")]
            if not os.path.exists(os.path.join(SHEETS, b + ".stepmesh.gz")):
                fks.append(b)
    if not fks:
        print("没有要处理的文件")
        return 0

    ok = 0
    for fk in fks:
        print("处理 %s" % fk[:12])
        if do_one(fk):
            ok += 1
    print("完成 %d / %d" % (ok, len(fks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
