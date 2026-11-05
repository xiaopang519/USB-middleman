# -*- coding: utf-8 -*-
"""example 03 —— 让板子真的动一下，并且**用效果验证**，不用回执。

这条示例想教你的是整个项目最贵的一课：

    回执 OK ≠ 事情发生了。

所以流程是：读光标 → 发 move → 再读光标 → 比差值。
差值对不上就报"没动"，不报成功。

前置：
    1. examples/02 已经通过
    2. 配置.json → 安全.允许移动 = true   （默认是 false，故意的）
    3. 光标能读（`cursor` 命令需要 手/代理/cursor_server.py 在会话 1 里起着；
       起不来的话本例会提示"读不到"，那也不算失败，只是无法自证）

    python examples/03_move_and_verify.py            # 向 右 移 60 px
    python examples/03_move_and_verify.py -60 0      # 向 左 移 60 px
"""
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "手"))

import hand_client as hc  # noqa: E402

光标正则 = re.compile(r"x=(-?\d+)\s+y=(-?\d+)")


def 读光标(h) -> tuple[int, int] | None:
    try:
        r = h.cmd("cursor")
    except Exception:                              # noqa: BLE001
        return None
    m = 光标正则.search(r or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def main() -> int:
    dx = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    dy = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    cfg = hc.load_cfg()
    命令 = "move %d %d" % (dx, dy)

    # 闸门只有一处：手/hand_client.py::guard()。这里显式问它，而不是自己写 if。
    ok, why = hc.guard(cfg, 命令)
    if not ok:
        print("闸门拒绝：%s" % why)
        print("要放开：配置.json → 安全.允许移动 = true（想清楚再改，板子会真的动鼠标）")
        return 2

    host, port = hc.node_addr(cfg, "A")
    print("目标 %s:%d，命令 `%s`" % (host, port, 命令))

    try:
        with hc.Hand(host, port, timeout=4.0) as h:
            前 = 读光标(h)
            print("动之前光标：", 前 if 前 else "读不到（cursor 助手没起）")

            回执 = h.cmd(命令)
            print("回执：", 回执)
            time.sleep(0.35)

            后 = 读光标(h)
            print("动之后光标：", 后 if 后 else "读不到")

        if 前 and 后:
            实际 = (后[0] - 前[0], 后[1] - 前[1])
            print("实际位移：", 实际)
            if 实际 == (dx, dy):
                print("✅ 对上了 —— 但注意：这不代表你的落点算对了，只代表板子在按你说的走")
                return 0
            print("❌ 对不上（期望 %s）。这就是为什么判据必须是坐标，不能是回执。" % ((dx, dy),))
            return 1
        print("回执拿到了，但读不到光标 → 无法自证。别把'没报错'当成功。")
        return 3
    except hc.HandBusy:
        print("板子被别的客户端占着（单客户端铁律）")
        return 4
    except hc.HandError as exc:
        print("连不上/断了：%s" % exc)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
