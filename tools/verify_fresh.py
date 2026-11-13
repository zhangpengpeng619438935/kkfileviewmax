#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证"源文件更新能不能被发现"（跑在网关容器里）。

    docker cp tools/verify_fresh.py kk-gateway:/tmp/ && docker exec kk-gateway python /tmp/verify_fresh.py

不碰任何真实文件，只验证判定逻辑：
  1. 指纹一致        → revalidate=True（续期，不重转）
  2. 指纹不一致      → revalidate=False（判定"变了"，走重转）
  3. path 带 ..      → 指纹为空（不允许 stat 到受控目录以外）
  4. 文件不存在      → 指纹为空
  5. 校验窗口        → CACHE_TTL_SECONDS 是配置的那个值
"""
import asyncio
import sys

sys.path.insert(0, "/app")

from app import config, db, engine  # noqa: E402

BIZ = ("http://127.0.0.1:8080/api/file/raw/%E8%87%AA%E5%8A%A8%E6%8A%95%E5%9B%BE"
       "%E6%A3%80%E6%B5%8B%E8%AE%BE%E5%A4%87%EF%BC%88%E6%89%8B%E5%8A%A8%E7%9B%AE"
       "%E8%A7%86%E6%8A%95%E5%9B%BE%EF%BC%89-%E5%B8%83%E5%B1%80%E5%B9%B3%E9%9D%A2"
       "%E5%9B%BE.DWG?path=1-3%20%E5%90%84%E9%83%A8%E9%97%A8%E8%AE%B0%E5%BD%95%E6"
       "%96%87%E4%BB%B6/%E8%AF%81%E4%B9%A6%E7%B1%BB/%E4%BA%BA%E5%91%98%E8%AF%81"
       "%E4%B9%A6/%E8%87%AA%E5%8A%A8%E6%8A%95%E5%9B%BE%E6%A3%80%E6%B5%8B%E8%AE%BE"
       "%E5%A4%87%EF%BC%88%E6%89%8B%E5%8A%A8%E7%9B%AE%E8%A7%86%E6%8A%95%E5%9B%BE%EF"
       "%BC%89-%E5%B8%83%E5%B1%80%E5%B9%B3%E9%9D%A2%E5%9B%BE.DWG")
FK = "5d42a6fa7e4baf643420bdda8d3ef3fe4065bb390e15e5150b9d4b80f01bd33d"
TRAV = "http://h/api/file/raw/x.DWG?path=../../../../etc/passwd"
MISS = "http://h/api/file/raw/y.DWG?path=no/such/file.DWG"


async def main():
    fp = engine.local_fingerprint(BIZ)
    assert fp, "读不到源文件指纹，/cephfs 挂载或 CEPH_ROOT 有问题"
    print("1) 指纹 = %r" % fp)
    assert await engine.revalidate(FK, BIZ) is True, "指纹一致却没续期"
    print("   一致 -> revalidate=True（续期，不重转）")

    r = await db.get_redis()
    key = "kk:srcfp:" + FK
    real = await r.get(key)
    await r.set(key, "1" + (real or ""), ex=120)      # 模拟"大小变了"
    try:
        assert await engine.revalidate(FK, BIZ) is False, "指纹不一致却续期了"
        print("2) 不一致 -> revalidate=False（判定变了，会重转）")
    finally:
        if real:
            await r.set(key, real, ex=86400 * 30)
        else:
            await r.delete(key)

    assert engine.local_fingerprint(TRAV) == "", "path 穿越没被拦住"
    print("3) path 带 .. -> 空（穿越已拦）")
    assert engine.local_fingerprint(MISS) == ""
    print("4) 文件不存在 -> 空")
    print("5) 校验窗口 = %d 秒" % config.CACHE_TTL_SECONDS)
    print("PASS")


asyncio.run(main())
