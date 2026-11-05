"""step_calib -- learn the screen-direction -> world-direction table by clicking.

The movement model in this client (as told by the operator):

    the mouse PULLS the character; the cursor only supplies a DIRECTION
    左键点一下   = one step, world delta 1
    左键按住     = continuous, +1 per step
    右键点一下   = one RUN step, world delta 2
    右键按住     = continuous run

Two consequences that invalidated the earlier approach:

* The DISTANCE is a fixed per-click amount.  It has nothing to do with how far
  the cursor sits from the character - so "scale the click offset by the
  remaining distance" is meaningless, and tuning hold time does nothing but
  decide how many extra steps sneak in.
* Because a single click is one step, navigation is a DISCRETE stepping problem.
  You do not need px-per-cell at all; you need to know which way to click.

So measure the table instead of assuming it.  For each of the eight screen
directions, click once and record the world delta.  Whatever isometric rotation
the client uses, it shows up in this table.

    python step_calib.py                 # left click, 8 directions
    python step_calib.py --button right  # run steps (2 cells)
    python step_calib.py --dist 140
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman          # noqa: E402
import reader                          # noqa: E402
import vision_color as vc              # noqa: E402

LEFT, RIGHT = 0x01, 0x02
CHAR_CY_RATIO = 0.385

DIRS = [("N", 0, -1), ("NE", 1, -1), ("E", 1, 0), ("SE", 1, 1),
        ("S", 0, 1), ("SW", -1, 1), ("W", -1, 0), ("NW", -1, -1)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_to(m, x, y, tol=3, tries=12):
    scale = getattr(m, "pixels_per_count", None) or m.calibrate()
    for _ in range(tries):
        cx, cy = cursor()
        dx, dy = x - cx, y - cy
        if (dx * dx + dy * dy) ** 0.5 <= tol:
            return True
        m.inject_mouse(dx=round(dx / scale), dy=round(dy / scale))
        time.sleep(0.015)
    return False


def read_pos(tries=6, gap=0.10):
    for _ in range(tries):
        c = reader.read_coord()
        if c and c[0] and c[1]:
            return c
        time.sleep(gap)
    return None


def settle(timeout=1.6):
    """Wait for the coordinate to stop changing."""
    last, stable, t0 = None, 0, time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.09)
        c = reader.read_coord()
        if c == last:
            stable += 1
            if stable >= 2:
                return c
        else:
            stable = 0
        last = c
    return last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--button", choices=["left", "right"], default="left")
    ap.add_argument("--dist", type=int, default=140,
                    help="how far from the character to click, in screen px")
    ap.add_argument("--hold", type=float, default=0.06,
                    help="press duration; a single click should be short or it "
                         "becomes a held move and steps more than once")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    btn = LEFT if args.button == "left" else RIGHT
    win = vc.find_window()
    if not win:
        print("game window not found")
        return 1
    cl, ct, cr, cb = vc.client_box(win[0])
    cw, ch = cr - cl, cb - ct
    ccx = cl + cw // 2
    ccy = ct + int(ch * CHAR_CY_RATIO)
    print("window %r  client %dx%d  character at screen (%d,%d)"
          % (win[1], cw, ch, ccx, ccy))
    print("button=%s  click distance=%dpx  hold=%.2fs  reps=%d"
          % (args.button, args.dist, args.hold, args.reps))
    print()

    results = []
    with Middleman().open(want_monitor=False) as m:
        m.pixels_per_count = m.calibrate()
        print("calibrated %.3f px/count" % m.pixels_per_count)
        print("start:", read_pos())
        print()

        for name, ux, uy in DIRS:
            for rep in range(args.reps):
                p0 = read_pos()
                if p0 is None:
                    print("  %-3s rep%d: cannot read before" % (name, rep))
                    continue
                n = math.hypot(ux, uy)
                ax = int(ccx + ux / n * args.dist)
                ay = int(ccy + uy / n * args.dist)
                move_to(m, ax, ay)
                m.inject_mouse(buttons=btn)
                time.sleep(args.hold)
                m.inject_mouse(buttons=0)
                settle()
                p1 = read_pos()
                if p1 is None:
                    print("  %-3s rep%d: cannot read after" % (name, rep))
                    continue
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                results.append({"dir": name, "rep": rep, "ux": ux, "uy": uy,
                                "from": list(p0), "to": list(p1),
                                "dx": dx, "dy": dy})
                print("  %-3s rep%d  aim(%+4d,%+4d)  %-11s -> %-11s  world (%+d,%+d)"
                      % (name, rep, ux, uy, "%d:%d" % p0, "%d:%d" % p1, dx, dy))
            print()

    # ---------------------------------------------------------------- summary
    print("  screen dir   world delta (median)   |d|")
    print("  " + "-" * 44)
    table = {}
    for name, ux, uy in DIRS:
        rows = [r for r in results if r["dir"] == name]
        if not rows:
            continue
        dx = sorted(r["dx"] for r in rows)[len(rows) // 2]
        dy = sorted(r["dy"] for r in rows)[len(rows) // 2]
        mag = math.hypot(dx, dy)
        table[name] = [dx, dy]
        print("  %-4s (%+2d,%+2d)   %-16s %5.1f" % (name, ux, uy, "(%+d,%+d)" % (dx, dy), mag))

    print()
    print("  This table IS the mapping.  A step is one world cell (left) or two")
    print("  (right), so navigation is: pick the screen direction whose world")
    print("  delta best matches the direction you still need to go.")
    print("  Note which world axes the screen axes actually drive - if screen E")
    print("  moves BOTH world x and y, the map is isometric and any steering that")
    print("  assumes 'screen right = +x only' will consistently go the wrong way.")

    out = args.out or ("step_table_%s.json" % args.button)
    json.dump({"button": args.button, "dist": args.dist, "hold": args.hold,
               "table": table, "raw": results},
              open(out, "w"), indent=1)
    print("\n  wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
