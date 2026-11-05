"""One-click check that the robot's "hand" works, with a message box.

Run this (or the Desktop shortcut).  It asks the board to move the mouse and
checks whether the cursor actually moved, then pops up a plain-language result
so you do not have to read console output.

Robust against a real mouse sitting on the desk: a high-DPI mouse drifts, and
that drift used to be counted as injected movement, producing random 2/4 or 3/4
results on an otherwise perfectly working board.  So we

  * tell the user to let go of the mouse first,
  * measure how far the cursor drifts on its own over the same interval,
  * subtract that from the observed movement, and
  * inject large movements so leftover noise cannot flip the sign.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MB_OK = 0x0
MB_ICONINFO = 0x40
MB_ICONERROR = 0x10

# Big enough that a drifting mouse cannot flip the sign (~2.6 px per count),
# small enough that the cursor never hits a screen edge from the centre.
STEP = 180
SETTLE = 0.20
MEASURE = 0.30
DRIFT_LIMIT = 60      # px of self-movement over MEASURE we consider "moving"


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor() -> tuple[int, int]:
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def setpos(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(x, y)


def box(text: str, title: str, flags: int = MB_OK | MB_ICONINFO) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def idle_drift(seconds: float = MEASURE) -> tuple[int, int]:
    x0, y0 = cursor()
    time.sleep(seconds)
    x1, y1 = cursor()
    return x1 - x0, y1 - y0


def run() -> tuple[bool, str]:
    from mcu_hid import Middleman

    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    cx, cy = sw // 2, sh // 2

    with Middleman().open(want_monitor=False) as m:
        if not m.has_inject():
            return False, ("板子上跑的不是带注入功能的固件。\n\n"
                           "请把桌面 RP2040固件 文件夹里的\n"
                           "「中介人固件+注入通道.uf2」烧进去。")

        scale = m.calibrate()
        note = "一个鼠标计数 = %.2f 像素" % scale

        # Where the cursor should end up, and a quarter of that as the minimum
        # convincing movement.  Also keeps the test away from the screen edges.
        expect = STEP * scale
        need = max(40.0, expect * 0.25)
        if cx - expect < 40 or cy - expect < 40:
            raise RuntimeError("screen too small for this test")

        # Is a physical mouse moving right now?  If so, the numbers are junk.
        setpos(cx, cy)
        time.sleep(SETTLE)
        dx, dy = idle_drift()
        if abs(dx) > DRIFT_LIMIT or abs(dy) > DRIFT_LIMIT:
            return False, ("检测到鼠标在动（不该动的时候自己移动了 %+d,%+d 像素）。\n\n"
                           "这不是板子的问题 —— 请把鼠标放稳、手离开，\n"
                           "然后再双击一次【验证机器人的手】。\n\n"
                           "（高 DPI 鼠标放在桌面上自己漂，这是正常的）" % (dx, dy))

        lines = [note, ""]
        tests = [(STEP, 0), (0, STEP), (-STEP, -STEP), (STEP, STEP)]
        ok = 0
        for tx, ty in tests:
            setpos(cx, cy)
            time.sleep(SETTLE)
            # how much the cursor moves on its own during one measurement window
            ddx, ddy = idle_drift()
            bx, by = cursor()
            m.inject_mouse(dx=tx, dy=ty)
            time.sleep(MEASURE)
            ax, ay = cursor()
            gx, gy = (ax - bx) - ddx, (ay - by) - ddy
            want = (1 if tx > 0 else -1 if tx < 0 else 0,
                    1 if ty > 0 else -1 if ty < 0 else 0)
            # only judge the axes we actually asked to move
            good = True
            if want[0] and (want[0] * gx < need):
                good = False
            if want[1] and (want[1] * gy < need):
                good = False
            ok += 1 if good else 0
            lines.append("  要求 (%+4d,%+4d)  实际 (%+5d,%+5d)%s  %s"
                         % (tx, ty, gx, gy,
                            "  [漂移 %+d,%+d]" % (ddx, ddy) if (ddx or ddy) else "",
                            "OK" if good else "方向不对"))

        lines += ["", "方向正确：%d/%d" % (ok, len(tests))]
        if ok == len(tests):
            return True, ("\n".join(lines) +
                          "\n\n板子可以自己动鼠标了 —— 机器人的手装好了。\n"
                          "（幅度会乘上你系统的鼠标速度设置，驱动已自动校准）")
        return False, ("\n".join(lines) +
                       "\n\n板子确实在发鼠标报文，但方向对不上。\n"
                       "再试一次；如果一直这样，打开桌面《★机器人的手-成功.txt》看看。")


def main() -> int:
    try:
        good, msg = run()
    except Exception:
        box("检查时出错：\n\n" + traceback.format_exc(), "验证失败", MB_OK | MB_ICONERROR)
        return 1
    box(msg, "机器人的手 —— 验证通过" if good else "机器人的手 —— 还没通",
        MB_OK | (MB_ICONINFO if good else MB_ICONERROR))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
