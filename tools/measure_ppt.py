# -*- coding: utf-8 -*-
"""在容器内实测 PPT → PDF 各阶段的耗时。

用法（容器内）: python3 /tmp/measure_ppt.py <pptx 绝对路径> [扩展名]
输出 JSON 行：每个 stage 的耗时、产物大小、页数。
"""
import json
import os
import shutil
import subprocess
import sys
import time

SOFFICE = "/opt/libreoffice26.8/program/soffice"
WORK = "/tmp/mppt"


def pages_of(pdf):
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(pdf)
        n = len(doc)
        doc.close()
        return n
    except Exception as exc:                                  # pragma: no cover
        return "err:%s" % exc


def conv(src, tag, rng="", ext="pptx"):
    outdir = os.path.join(WORK, tag)
    shutil.rmtree(outdir, ignore_errors=True)
    os.makedirs(outdir)
    prof = os.path.join(outdir, "prof")
    os.makedirs(prof)
    if rng:
        family = {"pptx": "impress", "ppt": "impress"}.get(ext, "impress")
        target = ('pdf:%s_pdf_Export:{"PageRange":{"type":"string","value":"%s"}}'
                  % (family, rng))
    else:
        target = "pdf"
    t0 = time.time()
    p = subprocess.run([SOFFICE, "--headless", "--norestore", "--nologo",
                        "--nodefault", "-env:UserInstallation=file://" + prof,
                        "--convert-to", target, "--outdir", outdir, src],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + ".pdf")
    ok = os.path.exists(pdf) and os.path.getsize(pdf) > 0
    return {"stage": tag, "sec": round(dt, 2), "ok": bool(ok),
            "pdf_kb": int(os.path.getsize(pdf) / 1024) if ok else 0,
            "pages": pages_of(pdf) if ok else 0,
            "out": (p.stdout or b"").decode("utf-8", "replace").strip()[:160]}


def main():
    src = sys.argv[1]
    ext = (sys.argv[2] if len(sys.argv) > 2 else "pptx").lower()
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK)
    print(json.dumps({"file": os.path.basename(src),
                      "size_mb": round(os.path.getsize(src) / 1048576, 1)},
                     ensure_ascii=False))
    # 先空转一次，把 LO 的字体缓存等一次性成本摘出去
    conv(src, "warm", "1", ext)
    for tag, rng in (("page1", "1"), ("page1_5", "1-5"), ("page1_15", "1-15"), ("all", "")):
        print(json.dumps(conv(src, tag, rng, ext), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
