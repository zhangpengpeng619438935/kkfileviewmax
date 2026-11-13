# -*- coding: utf-8 -*-
"""体检：线上有多少"源文件已经变了、但缓存还挂着 ready"的文件。

只读：mysql 取 ready 任务 → redis 取 kk:srcfp/kk:ready → 宿主机 stat 源文件 → 三方比对。
用来回答"预热/缓存会不会跟不上源文件变化"这个问题。

用法（127.0.0.1 宿主机，/opt/kkfileview-service）：
    python3 tools/dev/fp_drift.py --limit 80
"""
import argparse
import base64
import subprocess
import urllib.parse
from datetime import datetime, timezone

CEPH = "/cephfs_data/fileManagement"     # URL 里的 /files/... 就挂在它下面


def sh(cmd, inp=None):
    p = subprocess.run(cmd, shell=True, input=inp, capture_output=True, timeout=180)
    return p.stdout, p.stderr


def mysql(sql):
    cmd = ('docker exec kk-mysql mysql -ukkfileview -pchange-me-db-password kkfileview '
           '--default-character-set=utf8mb4 -N -e %s' % sq(sql))
    out, err = sh(cmd)
    if not out and err:
        raise SystemExit(err.decode("utf-8", "replace")[:400])
    return out.decode("utf-8", "replace").strip()


def sq(s):
    q = "'"
    return q + s.replace("'", "'\"'\"'") + q


def rcli(*args):
    out, _ = sh("docker exec kk-redis redis-cli -n 3 " + " ".join(args))
    return out


def mget(keys):
    """raw 输出：缺键显示空行，不能按 (nil) 判断。"""
    if not keys:
        return {}
    out, _ = sh("docker exec kk-redis redis-cli -n 3 mget " + " ".join(keys))
    vals = out.decode("utf-8", "replace").splitlines()
    return dict(zip(keys, vals))


def rel_of(file_url):
    """业务地址两种形态：
    - http://127.0.0.1:8090/files/<相对路径>   ← 线上绝大多数
    - http://.../xxx?path=/受控文件下的相对路径  ← 预转脚本自己拼的
    """
    u = urllib.parse.urlparse(file_url)
    q = urllib.parse.parse_qs(u.query)
    if (q.get("path") or [""])[0]:
        return ("受控文件", (q.get("path") or [""])[0])
    p = urllib.parse.unquote(u.path)
    if p.startswith("/files/"):
        return ("files", p[len("/files/"):])
    return ("", "")


def fp_of(kind, rel):
    base = {"files": CEPH + "/files", "受控文件": CEPH + "/files/示例事业部/文档中心/受控文件"}.get(kind, "")
    if not base:
        return ""
    try:
        st = __import__("os").stat(base + "/" + rel.lstrip("/"))
    except OSError:
        return ""
    gmt = datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT")
    return "%d|%s|" % (st.st_size, gmt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80)
    a = ap.parse_args()

    rows = mysql(
        "SELECT file_key, MAX(file_url), MAX(file_name) FROM preview_task"
        " WHERE status='ready'"
        " GROUP BY file_key ORDER BY MAX(ready_at) DESC LIMIT %d" % a.limit)
    items = []
    for line in rows.splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        kind, rel = rel_of(p[1])
        if rel:
            items.append({"fk": p[0], "kind": kind, "rel": rel, "name": p[2]})
    if not items:
        print("没有带 path 参数的 ready 任务")
        return

    fks = [i["fk"] for i in items]
    fp_map = mget(["kk:srcfp:" + k for k in fks])
    rd_map = mget(["kk:ready:" + k for k in fks])

    same = drift = nofp = nolocal = 0
    drifts = []
    for i in items:
        saved = (fp_map.get("kk:srcfp:" + i["fk"]) or "").strip()
        cur = fp_of(i["kind"], i["rel"])
        if not saved:
            nofp += 1
            continue
        if not cur:
            nolocal += 1
            continue
        # 存下来的可能是 HTTP 三段式（多一个 ETag），本地只能给两段：只比前两段
        if saved.split("|")[:2] == cur.split("|")[:2]:
            same += 1
        else:
            drift += 1
            drifts.append((i["name"], saved, cur,
                           (rd_map.get("kk:ready:" + i["fk"]) or "").strip()))

    print("抽样 %d 个 ready 文件：" % len(items))
    print("  指纹一致（缓存有效）%d" % same)
    print("  指纹不一致（源已变，缓存仍是旧的）%d" % drift)
    print("  无指纹记录（下次过期必重转）%d" % nofp)
    print("  源文件已不在本地 %d" % nolocal)
    for name, s, c, rd in drifts[:15]:
        print("  - %s\n      旧 %s\n      新 %s\n      ready=%s" % (name[:50], s, c, rd or "-"))


if __name__ == "__main__":
    main()
