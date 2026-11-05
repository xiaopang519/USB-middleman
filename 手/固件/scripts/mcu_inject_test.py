"""Prove the middleman can move the mouse by itself -- the bot's "hand".

Reads the real cursor position, asks the board to emit a relative mouse report
through the patched INJECT command, then reads the cursor again.

Two things this test must do or it will lie to you (see reference/pitfalls.md):

  * measure and subtract the cursor's own drift, because a high-DPI mouse
    resting on a desk moves the cursor by itself.  Without this, a perfectly
    working board scores a random 2/4 or 3/4.
  * inject a movement large enough that leftover noise cannot flip the sign.

Never clicks, so it cannot press anything in whatever window is under the
pointer.

    python mcu_inject_test.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman  # noqa: E402

STEP = 180          # mouse counts per test
SETTLE = 0.20
MEASURE = 0.30
DRIFT_LIMIT = 60    # px of self-movement we consider "the user is moving it"


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor() -> tuple[int, int]:
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def setpos(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(x, y)


def drift(seconds: float = MEASURE) -> tuple[int, int]:
    x0, y0 = cursor()
    time.sleep(seconds)
    x1, y1 = cursor()
    return x1 - x0, y1 - y0


def main() -> int:
    sw = ctypes.windll.user32.GetSystemMetrics(0)
    sh = ctypes.windll.user32.GetSystemMetrics(1)
    cx, cy = sw // 2, sh // 2
    print("screen: %dx%d" % (sw, sh))

    with Middleman().open(want_monitor=False) as m:
        print("config version :", m.get_version())
        if not m.has_inject():
            print("inject support : False")
            print("\nFAIL: board is running stock firmware (no INJECT command)")
            return 1
        print("inject support : True")

        scale = m.calibrate()
        expect = STEP * scale
        need = max(40.0, expect * 0.25)
        print("calibration    : %.3f px per count  (expect ~%.0f px per move)" % (scale, expect))
        print("                (set the pointer-speed slider to the middle for 1:1)")

        setpos(cx, cy)
        time.sleep(SETTLE)
        ddx, ddy = drift()
        if abs(ddx) > DRIFT_LIMIT or abs(ddy) > DRIFT_LIMIT:
            print("\nFAIL: the physical mouse is moving (%+d,%+d px on its own)." % (ddx, ddy))
            print("      Let go of the mouse and run again -- this is not a board problem.")
            return 1

        print()
        ok = 0
        tests = [(STEP, 0), (0, STEP), (-STEP, -STEP), (STEP, STEP)]
        for tx, ty in tests:
            setpos(cx, cy)
            time.sleep(SETTLE)
            ddx, ddy = drift()
            bx, by = cursor()
            t0 = time.time()
            m.inject_mouse(dx=tx, dy=ty)
            time.sleep(MEASURE)
            ax, ay = cursor()
            gx, gy = (ax - bx) - ddx, (ay - by) - ddy
            want = (1 if tx > 0 else -1 if tx < 0 else 0,
                    1 if ty > 0 else -1 if ty < 0 else 0)
            good = True
            if want[0] and want[0] * gx < need:
                good = False
            if want[1] and want[1] * gy < need:
                good = False
            ok += 1 if good else 0
            print("  asked (%+4d,%+4d) -> got (%+5d,%+5d)%s  %s   [%.0f ms]"
                  % (tx, ty, gx, gy,
                     "  [drift %+d,%+d]" % (ddx, ddy) if (ddx or ddy) else "",
                     "OK" if good else "WRONG", (time.time() - t0) * 1000))

        print()
        if ok == len(tests):
            print("PASS: %d/%d -- the board moves the mouse on command." % (ok, len(tests)))
            print("      The bot has a hardware-level hand again.")
            return 0
        print("FAIL: only %d/%d directions were right." % (ok, len(tests)))
        print("      The board is emitting but the host may not be delivering;")
        print("      see reference/pitfalls.md #3 (check the official firmware first).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
