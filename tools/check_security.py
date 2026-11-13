#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""令牌与权限的最小自检（容器内跑，不依赖 pytest）。

    docker exec kk-gateway python3 /opt/kkfileview-service/tools/check_security.py

退出码 0 = 全过；非 0 = 有断言失败。放在 crontab 或发布后跑一次，
能挡住"改签名校验时顺手把过期判断删了"这类回归 —— 这类改动上线后
表现是"链接永久有效"，没人会主动发现。

只测纯函数（不连 Redis / MySQL），所以随时可跑。
"""
import sys

sys.path.insert(0, "/app")

from fastapi import HTTPException  # noqa: E402

from app import config, security  # noqa: E402

_fail = 0


def check(name, fn):
    global _fail
    try:
        fn()
        print("  OK   %s" % name)
    except AssertionError as exc:
        _fail += 1
        print("  FAIL %s: %s" % (name, exc))
    except Exception as exc:                     # 未预期的异常同样是失败
        _fail += 1
        print("  FAIL %s: %s: %s" % (name, type(exc).__name__, exc))


def expect_401(fn):
    try:
        fn()
    except HTTPException as exc:
        assert exc.status_code == 401, "应为 401，实际 %s" % exc.status_code
        return
    raise AssertionError("应当抛 401，但没有抛")


def main():
    print("SECRET_KEY 是默认值: %s（默认值上线属于安全问题）"
          % (config.SECRET_KEY == "change-me-please-kkfileview-2026"))
    print("ADMIN_TOKEN 是默认值: %s" % (config.ADMIN_TOKEN == "change-me-admin-token"))

    tok = security.make_token("kk-demo", ttl=60)

    def parse_ok():
        assert security.parse_token(tok).get("app") == "kk-demo", "app 字段不对"

    check("正常令牌可解析", parse_ok)

    def tampered():
        payload, sig = tok.rsplit(".", 1)
        bad = payload + "." + ("0" if sig[0] != "0" else "1") + sig[1:]
        expect_401(lambda: security.parse_token(bad))

    check("改签名被拒", tampered)

    def expired():
        old = security.make_token("kk-demo", ttl=-10)
        expect_401(lambda: security.parse_token(old))

    check("过期令牌被拒", expired)

    def malformed():
        for bad in ("", "nonsense", "a.b"):
            expect_401(lambda b=bad: security.parse_token(b))

    check("空/畸形令牌被拒", malformed)

    def cross_use():
        # 三类令牌共用同一把签名密钥，必须靠 typ 区分。
        # 预览令牌会出现在地址栏 / iframe src / Referer 里，能当访问令牌用就是越权。
        pv = security.make_preview_token("f" * 64, "EMP0001", "http://x/a.pdf", "kk-demo")
        expect_401(lambda: security.parse_token(pv))
        assert security.parse_preview_token(pv)["fk"] == "f" * 64, "预览令牌本身要能解析"

        adm = security.make_admin_session()
        expect_401(lambda: security.parse_token(adm))
        assert security.is_admin_token(adm) is True, "管理员会话应被识别"

    check("三类令牌不能混用", cross_use)

    def viewer_roles():
        # 访客能看全站，但不是管理员（admin 只用于"能改配置"）
        guest = {"app_id": "_all", "app_name": "访客", "admin": False,
                 "scope": "_all", "anonymous": True}
        assert security.can_all(guest) is True, "访客应能看全站"
        assert security.viewer_scope(guest, "some-app") == "some-app"
        tenant = {"app_id": "app-a", "admin": False, "scope": "app-a"}
        assert security.can_all(tenant) is False, "普通系统不应看全站"
        assert security.viewer_scope(tenant, "app-b") == "app-a", "不能越权查别的系统"

    check("访客/普通系统的数据范围", viewer_roles)

    def ip_prefix_not_leaky():
        # 这一条现在测的是"提醒"：等 IP 白名单修成 CIDR 后应改为断言拒绝
        acc = {"ip_whitelist": "127.0.0.1"}
        try:
            security.check_ip_whitelist(acc, "127.0.0.1")
            print("  WARN IP 白名单仍是前缀匹配：127.0.0.1 放行了 127.0.0.1")
        except HTTPException:
            pass

    check("IP 白名单（当前行为记录）", ip_prefix_not_leaky)

    print("\n%s" % ("全部通过" if _fail == 0 else "失败 %d 项" % _fail))
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
