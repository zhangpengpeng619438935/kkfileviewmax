#!/usr/bin/env python3
"""按需写入 .env 的键值(不改其他行)。用法: python3 tools/apply_env.py K=V [K=V ...]"""
import io
import sys

path = "/opt/kkfileview-service/.env"
with io.open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()

wanted = {}
for arg in sys.argv[1:]:
    k, _, v = arg.partition("=")
    wanted[k.strip()] = v.strip()

out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else None
    if key in wanted:
        out.append("%s=%s" % (key, wanted[key]))
        seen.add(key)
    else:
        out.append(line)
for k, v in wanted.items():
    if k not in seen:
        out.append("%s=%s" % (k, v))

with io.open(path, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

for line in out:
    if line.split("=", 1)[0] in wanted:
        print(line)
