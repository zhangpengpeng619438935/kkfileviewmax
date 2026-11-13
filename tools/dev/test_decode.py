#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 STEP 名称解码（ISO 10303-21 的 \\X2\\ 转义）。"""
BS = chr(92)


def decode_step_name(s):
    out = []
    i = 0
    X2 = BS + "X2" + BS
    X4 = BS + "X4" + BS
    X0 = BS + "X0" + BS
    while i < len(s):
        if s.startswith(X2, i) or s.startswith(X4, i):
            x2 = s.startswith(X2, i)
            j = s.find(X0, i + 4)
            if j < 0:
                out.append(s[i:])
                break
            hx = s[i + 4:j]
            try:
                out.append(bytes.fromhex(hx).decode(
                    "utf-16-be" if x2 else "utf-32-be", "replace"))
            except ValueError:
                out.append(hx)
            i = j + 4
        elif s.startswith(BS * 2, i):
            out.append(BS)
            i += 2
        elif s.startswith("''", i):
            out.append("'")
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out).strip()


TESTS = [
    BS + "X2" + BS + "76f47ebf8f74627fff1a" + BS + "X0" + BS + "LMK20LUU"
    + BS + "X2" + BS + "5c3a5bf8" + BS + "X0" + BS + "20X32X80",
    "80022100-DRW-002-00.1 W04" + BS + "X2" + BS + "76ee89c653f067b6ff0865b07ebf4f53ff09"
    + BS + "X0" + BS + "-" + BS + "X2" + BS + "76ae5e26963262a47f69" + BS + "X0" + BS + "_"
    + BS + "X2" + BS + "9ed88ba4" + BS + "X0" + BS,
    BS + "X2" + BS + "53554f4d5f00517376d2" + BS + "X0" + BS + "-"
    + BS + "X2" + BS + "88c5914d4f53" + BS + "X0" + BS,
    "plain-ascii-name",
]

if __name__ == "__main__":
    for t in TESTS:
        print(repr(t[:40]), "->", decode_step_name(t))
