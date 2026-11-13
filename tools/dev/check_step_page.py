# -*- coding: utf-8 -*-
"""改完 step_view.py 的页面模板，先跑这个再部署。

背景：step_view 的 HTML 是一整个 `%` 格式化的字符串，模板里任何单个 `%`
（比如注释里写"每格 5%"）都会被当成格式符，线上直接 500：
    TypeError: not enough arguments for format string
这个错误 py_compile 查不出来，只有真正渲染时才炸 —— 2026-11-05 就踩过一次。

用法：
    python tools/dev/check_step_page.py
"""
import ast
import sys

TARGET = "gateway/app/step_view.py"


def main() -> int:
    try:
        src = open(TARGET, encoding="utf-8").read()
    except OSError as exc:
        print("读不到 %s: %s" % (TARGET, exc))
        return 2
    tree = ast.parse(src)
    found = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Return) and isinstance(node.value, ast.BinOp)):
            continue
        v = node.value
        if not (isinstance(v.op, ast.Mod) and isinstance(v.left, ast.Constant)
                and isinstance(v.left.value, str)
                and "<!DOCTYPE html>" in v.left.value):
            continue
        tpl, right = v.left.value, v.right
        keys = []
        if isinstance(right, ast.Dict):
            keys = [k.value for k in right.keys if isinstance(k, ast.Constant)]
        elif isinstance(right, ast.Name):
            # 运行时才知道键名，只能用常见占位符兜底
            import re
            keys = sorted(set(re.findall(r"%\((\w+)\)s", tpl)))
        found += 1
        try:
            out = tpl % {k: "X" for k in keys}
        except (TypeError, ValueError) as exc:
            line = tpl[:200].replace("\n", " ")
            print("模板格式化失败: %s" % exc)
            print("  模板片段: %s..." % line)
            print("  提示：模板里字面量百分号要写成两个百分号；"
                  "注释里出现的单个百分号也会触发，重点看新加的注释。")
            return 1
        print("模板格式化 OK：%d 字符 → %d 字符（占位 %s）"
              % (len(tpl), len(out), ", ".join(keys) or "无"))
    if not found:
        print("没找到页面模板，确认 TARGET 路径是否变了")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
