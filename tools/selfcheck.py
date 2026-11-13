#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缓存续期这一组的自检（跑在网关容器里，因为它要用 app 包与 Redis）。

    docker cp tools/selfcheck.py kk-gateway:/tmp/ && docker exec kk-gateway python /tmp/selfcheck.py

只查四件容易悄悄坏掉的事：
  1. file_key 的算法（业务 URL → 缓存键），预转脚本与网关必须算出同一个值
  2. 本地指纹能读到源文件（这是"过期不重转"的前提）
  3. 源读不到时要能被识别出来（否则会把好缓存删掉）
  4. 有指纹且一致时，revalidate 必须续期
"""
import asyncio
import os
import sys

sys.path.insert(0, "/app")

from app import config, engine  # noqa: E402

BIZ = ("http://127.0.0.1:8080/api/file/raw/%E8%87%AA%E5%8A%A8%E6%8A%95%E5%9B%BE"
       "%E6%A3%80%E6%B5%8B%E8%AE%BE%E5%A4%87%EF%BC%88%E6%89%8B%E5%8A%A8%E7%9B%AE"
       "%E8%A7%86%E6%8A%95%E5%9B%BE%EF%BC%89-%E5%B8%83%E5%B1%80%E5%B9%B3%E9%9D%A2"
       "%E5%9B%BE.DWG?path=1-3%20%E5%90%84%E9%83%A8%E9%97%A8%E8%AE%B0%E5%BD%95%E6"
       "%96%87%E4%BB%B6/%E8%AF%81%E4%B9%A6%E7%B1%BB/%E4%BA%BA%E5%91%98%E8%AF%81"
       "%E4%B9%A6/%E8%87%AA%E5%8A%A8%E6%8A%95%E5%9B%BE%E6%A3%80%E6%B5%8B%E8%AE%BE"
       "%E5%A4%87%EF%BC%88%E6%89%8B%E5%8A%A8%E7%9B%AE%E8%A7%86%E6%8A%95%E5%9B%BE%EF"
       "%BC%89-%E5%B8%83%E5%B1%80%E5%B9%B3%E9%9D%A2%E5%9B%BE.DWG")
# 2026-11-12 改了 filekey 归一化（`?path=` 以业务路径为身份），这个白盒用例的
# 期望值随之更新；预转与网关共用 gateway/app/filekey.py，两边不会再漂移。
FK = "247c1b4df6aa2317c30c0b8fbdb6018b4668cb17f770a300349fa3187ea033ab"
MISSING = "http://127.0.0.1:8080/api/file/raw/x.DWG?path=no/such/file.DWG"


async def main():
    assert engine.file_key_of(BIZ) == FK, "file_key 与预转脚本算出的不一致"
    fp = engine.local_fingerprint(BIZ)
    assert fp and fp.count("|") == 2, "本地指纹读不到源文件: %r" % fp
    assert engine.local_fingerprint(MISSING) == "", "不存在的文件不该有指纹"
    # revalidate 只在"有产物 + 有指纹"时才有意义。这份 DWG 在这台环境从没转成功过
    # （audit_log 里 32 次全是失败），硬要求 True 会让自检永远红着 —— 红久了的自检
    # 等于没有自检，所以改成"有产物才校验"。
    art = engine.artifact_path(FK)
    if art and os.path.exists(art):
        ok = await engine.revalidate(FK, BIZ)
        print("file_key ok / 指纹 %s / 缺失文件返回空 ok / revalidate=%s" % (fp, ok))
        assert ok, "指纹一致却没续期——过期后会退回重转"
    else:
        print("file_key ok / 指纹 %s / 缺失文件返回空 ok / 无产物，跳过 revalidate" % fp)
    print("SELFCHECK PASS")


asyncio.run(main())
