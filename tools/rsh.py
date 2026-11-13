# -*- coding: utf-8 -*-
"""SSH 远程执行/传文件小工具（内网部署用）。

用法:
  python tools/rsh.py run "docker ps"
  python tools/rsh.py put <本地文件> <远端路径>
  python tools/rsh.py get <远端文件> <本地路径>
环境变量: RSH_HOST(默认 127.0.0.1) RSH_USER(kkfileview) RSH_PASS(change-me-db-password)
"""
import os
import sys

import paramiko

HOST = os.environ.get("RSH_HOST", "127.0.0.1")
USER = os.environ.get("RSH_USER", "kkfileview")
PWD = os.environ.get("RSH_PASS", "change-me-db-password")


def conn():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=15,
              banner_timeout=20, auth_timeout=20)
    return c


def run(cmd, timeout=None):
    # 长任务（拆件解析一次要几分钟）用 RSH_TIMEOUT 放宽；默认 600 秒
    timeout = timeout or int(os.environ.get("RSH_TIMEOUT", "600"))
    c = conn()
    try:
        _, out, err = c.exec_command(cmd, timeout=timeout)
        o = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        rc = out.channel.recv_exit_status()
        sys.stdout.write(o)
        if e.strip():
            sys.stderr.write(e)
        return rc
    finally:
        c.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    act = sys.argv[1]
    c = conn()
    try:
        if act == "run":
            return run(sys.argv[2])
        if act == "put":
            # 该账号的 SFTP 子系统不可用，改走 exec 的 stdin 通道：
            # cat > 目标文件，直接把字节流写进去。
            local, remote = sys.argv[2], sys.argv[3]
            with open(local, "rb") as f:
                data = f.read()
            # .sh 在 Windows 上被写成 CRLF，传到 Linux 后 bash 会报
            # "$'\r': command not found" / "syntax error near unexpected token"。
            # 这里统一转成 LF，省得每次传完还要上去 dos2unix。
            if remote.endswith(".sh"):
                data = data.replace(b"\r\n", b"\n")
            stdin, out, err = c.exec_command("cat > '%s'" % remote, timeout=120)
            stdin.write(data)
            stdin.channel.shutdown_write()
            rc = out.channel.recv_exit_status()
            e = err.read().decode("utf-8", "replace").strip()
            if rc != 0:
                print("put 失败 rc=%s %s" % (rc, e))
                return rc
            print("put ok -> %s (%d 字节)" % (remote, len(data)))
            return 0
        if act == "get":
            remote, local = sys.argv[2], sys.argv[3]
            _, out, _ = c.exec_command("cat '%s'" % remote, timeout=120)
            with open(local, "wb") as f:
                f.write(out.read())
            print("get ok ->", local)
            return 0
        print("未知动作", act)
        return 2
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
