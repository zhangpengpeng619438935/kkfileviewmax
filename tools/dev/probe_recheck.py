# -*- coding: utf-8 -*-
"""验证 prewarm 的"复查触发结果 → 记冷却"链路（宿主机跑，不需要容器）。"""
import sys

sys.path.insert(0, "/opt/kkfileview-service/tools")
import prestate          # noqa: E402
import prewarm_hot as pw  # noqa: E402

FK = "7033ce0232644ab6b41115f13a3b3f547537edf5524eb5a1643e9e3c4980d060"

print("task_state  ->", repr(pw.task_state(FK)))
print("task_error  ->", repr(pw.task_error(FK)))
print("之前冷却    ->", repr(prestate.blocked(FK)))

kind = prestate.kind_of(pw.task_error(FK) or "转换失败")
st, loud = prestate.note_fail(FK, kind, pw.task_error(FK) or "转换失败", "")
print("note_fail   -> kind=%s n=%s 冷却到 %s"
      % (kind, st.get("n"),
         __import__("time").strftime("%m-%d %H:%M",
                                     __import__("time").localtime(st.get("until", 0)))))
print("之后冷却    ->", repr(prestate.blocked(FK)))
