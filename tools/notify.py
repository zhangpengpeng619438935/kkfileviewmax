#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行推送一句话到群机器人 —— 纯推送工具，**不负责记录**。

定位：告警的"记录"由 `tools/cron_run.py` 写进 `alert_log`（看板「服务告警」），
这里只负责"喊一声"。目前没配群机器人，所以它的实际作用只是提供一个可选的
推送出口；cron_run 内部会调用 `send()`，配了 `ALERT_WEBHOOK` 才真的发。

**为什么不用 gateway 的 alerts 模块**：cron 跑在宿主机，import 不了容器里的
`app.*`（依赖 httpx/aiomysql）。这里只用标准库，读同一份 .env 的 ALERT_WEBHOOK。

用法：
    python3 tools/notify.py "数据库备份失败"
    python3 tools/notify.py "缓存回收异常" "退出码 2，见 /tmp/kk_gc.log"
    tail -3 /tmp/kk_gc.log | python3 tools/notify.py "缓存回收异常" -   # 正文从 stdin

    **正文不会自动从 stdin 读**：cron 下 stdin 不是 tty，读它会永久阻塞 ——
    备份一旦失败就卡在这里，flock 不释放，之后每天的备份全部被跳过。
    要 stdin 必须显式传 `-`。

退出码：0 推送成功或未配置通道；1 推送失败（方便 cron 察觉）。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return default


def payload(text):
    kind = env("ALERT_WEBHOOK_KIND", "wecom").strip().lower()
    if kind == "dingtalk":
        return {"msgtype": "markdown",
                "markdown": {"title": "预览服务告警", "text": text}}
    if kind == "raw":
        return {"text": text}
    return {"msgtype": "markdown", "markdown": {"content": text}}


def send(text, timeout=8):
    url = env("ALERT_WEBHOOK").strip()
    if not url:
        return 0                    # 没配通道：不算失败，静默退出
    data = json.dumps(payload(text), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        print("推送失败 HTTP %s: %s" % (e.code, e.read()[:160]), file=sys.stderr)
        return 1
    except Exception as exc:
        print("推送失败: %s" % exc, file=sys.stderr)
        return 1
    try:
        j = json.loads(body)
    except Exception:
        return 0
    code = j.get("errcode", j.get("code", 0))
    if code not in (0, "0", None):
        print("推送被拒: %s" % body[:160], file=sys.stderr)
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    title = args[0]
    body = ""
    if len(args) > 1:
        if args[1] == "-":
            body = sys.stdin.read().strip()      # 显式要求才读，见文件头
        else:
            body = " ".join(args[1:])
    if body:
        body = body[-1500:]         # 群里不需要整份日志
        text = "⚠️ **%s**\n\n> %s" % (title, body.replace("\n", "\n> "))
    else:
        text = "⚠️ **%s**" % title
    return send(text)


if __name__ == "__main__":
    sys.exit(main())
