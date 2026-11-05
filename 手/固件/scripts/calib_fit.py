"""calib_fit -- derive the screen<->world mapping from real movement.

`calib_grid.json` is a hand-calibrated 2x2 matrix, and one of its rows is
`[0, 200, 0, 0, "S"]` - a South calibration that recorded no movement at all.
If the mapping that decides "which way do I actually walk" is wrong, the bot
walks confidently in the wrong direction, which is exactly the shape of the
"character ends up 38.5 cells off route" symptom.

So measure it instead of trusting it.  Two facts make this clean:

1. The character runs toward the cursor while the right button is held
   (lb_nav.py).  So pointing the cursor in direction d makes it move in
   direction d, in *cursor* space.
2. The character's screen position is unknown (it is not the client centre),
   but it is CONSTANT within a run.  Subtracting opposite directions cancels it:

       world(E) - world(W) = M @ (2d, 0)
       world(S) - world(N) = M @ (0, 2d)

   No need to know where the character is drawn - the unknown drops out.

    python calib_fit.py --reps 3 --px 300 --hold 1.4
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman          # noqa: E402
import reader as co                    # colon-anchored reader (see coord_v2.py)
import vision_color as vc              # noqa: E402

RIGHT = 0x02


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor():
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def move_to(m, x, y, tol=3, tries=10):
    scale = getattr(m, "pixels_per_count", None) or m.calibrate()
    for _ in range(tries):
        cx, cy = cursor()
        dx, dy = x - cx, y - cy
        if (dx * dx + dy * dy) ** 0.5 <= tol:
            return True
        m.inject_mouse(dx=round(dx / scale), dy=round(dy / scale))
        time.sleep(0.015)
    return False


def read_coord_hard(tries=5):
    for _ in range(tries):
        c = co.read_coord()
        if c and c[0] and c[1]:
            return c
        time.sleep(0.08)
    return None


def wait_stable(timeout=1.5):
    """Wait until the coordinate stops changing, i.e. the character has stopped."""
    last, stable, t0 = None, 0, time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.1)
        c = co.read_coord()
        if c == last:
            stable += 1
            if stable >= 2:
                return c
        else:
            stable = 0
        last = c
    return last


DIRS = [("E", 1, 0), ("W", -1, 0), ("N", 0, -1), ("S", 0, 1)]
DIAG = [("NE", 1, -1), ("NW", -1, -1), ("SE", 1, 1), ("SW", -1, 1)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--px", type=int, default=300)
    ap.add_argument("--hold", type=float, default=1.4)
    ap.add_argument("--settle", type=float, default=0.8)
    ap.add_argument("--out", default="calib_measured.json")
    ap.add_argument("--dirs", type=int, default=4, choices=[4, 8],
                    help="4 cardinals, or 8 including diagonals (useful when "
                         "walls block some directions)")
    ap.add_argument("--arrive", action="store_true", default=True,
                    help="hold the button until the coordinate stops changing, so "
                         "the measured distance is the real one and not just how far "
                         "it got before the timer fired")
    ap.add_argument("--max-hold", type=float, default=4.0)
    args = ap.parse_args()

    dirs = DIRS + (DIAG if args.dirs == 8 else [])

    win = vc.find_window()
    if not win:
        print("game window not found")
        return 1
    hwnd, title = win[0], win[1]
    cl, ct, cr, cb = vc.client_box(hwnd)
    cw, ch = cr - cl, cb - ct
    ccx, ccy = cl + cw // 2, ct + ch // 2
    print("window %r  client %dx%d at (%d,%d)  centre (%d,%d)"
          % (title, cw, ch, cl, ct, ccx, ccy))

    data = []
    with Middleman().open(want_monitor=False) as m:
        m.pixels_per_count = m.calibrate()
        print("calibrated %.3f px/count\n" % m.pixels_per_count)
        for rep in range(args.reps):
            for name, ux, uy in dirs:
                a = read_coord_hard()
                if a is None:
                    print("  %s rep%d: cannot read before" % (name, rep))
                    continue
                move_to(m, ccx + ux * args.px, ccy + uy * args.px)
                m.inject_mouse(buttons=RIGHT)
                if args.arrive:
                    # Hold until the coordinate stops moving: the character has
                    # arrived.  Releasing on a timer measures the run so far, not
                    # the requested distance, which corrupts the scale of M.
                    t_hold = time.time()
                    last, stable = None, 0
                    while time.time() - t_hold < args.max_hold:
                        time.sleep(0.13)
                        cnow = co.read_coord()
                        if cnow == last:
                            stable += 1
                            if stable >= 3:
                                break
                        else:
                            stable = 0
                        last = cnow
                else:
                    time.sleep(args.hold)
                m.inject_mouse(buttons=0)
                wait_stable(1.5)
                time.sleep(args.settle)
                b = read_coord_hard()
                if b is None:
                    print("  %s rep%d: cannot read after" % (name, rep))
                    continue
                dX, dY = b[0] - a[0], b[1] - a[1]
                data.append({"dir": name, "rep": rep, "ux": ux, "uy": uy,
                             "px": args.px, "from": list(a), "to": list(b),
                             "dX": dX, "dY": dY})
                print("  %s rep%d  %s -> %s   d=(%+d,%+d)" % (name, rep, a, b, dX, dY))
        m.inject_mouse(buttons=0)

    json.dump(data, open(args.out, "w"), indent=1)
    print("\nraw data -> %s" % args.out)

    # ---------------------------------------------------------------- fit M
    def mean_delta(d):
        rows = [r for r in data if r["dir"] == d]
        if not rows:
            return None
        return (float(np.mean([r["dX"] for r in rows])),
                float(np.mean([r["dY"] for r in rows])), len(rows))

    E, W, N, S = mean_delta("E"), mean_delta("W"), mean_delta("N"), mean_delta("S")
    print("\n  mean world delta per direction (cells)")
    for nm, v in (("E", E), ("W", W), ("N", N), ("S", S)):
        if v:
            print("    %s  d=(%+6.2f,%+6.2f)  n=%d" % (nm, v[0], v[1], v[2]))
    if not all((E, W, N, S)):
        print("not enough data to fit")
        return 1

    # opposite pairs cancel the unknown character position
    dEW = (E[0] - W[0], E[1] - W[1])          # = M @ (2d, 0)
    dSN = (S[0] - N[0], S[1] - N[1])          # = M @ (0, 2d)
    two_d = 2.0 * args.px
    col1 = (dEW[0] / two_d, dEW[1] / two_d)   # cells per screen px, cursor-x
    col2 = (dSN[0] / two_d, dSN[1] / two_d)   # cells per screen px, cursor-y
    M = np.array([[col1[0], col2[0]], [col1[1], col2[1]]])
    print("\n  measured M (world cells per screen pixel):")
    print("      dX = %+.6f * sx  %+.6f * sy" % (M[0, 0], M[0, 1]))
    print("      dY = %+.6f * sx  %+.6f * sy" % (M[1, 0], M[1, 1]))
    print("      px per cell: x=%.1f  y=%.1f"
          % (1 / max(1e-9, abs(M[0, 0])), 1 / max(1e-9, abs(M[1, 1]))))

    # ------------------------------------------------- compare with the file
    try:
        old = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "calib_grid.json"), encoding="utf-8"))
        om = np.array(old["matrix"], dtype=float)
        print("\n  stored calib_grid.json matrix:")
        print("      dX = %+.6f * sx  %+.6f * sy" % (om[0, 0], om[0, 1]))
        print("      dY = %+.6f * sx  %+.6f * sy" % (om[1, 0], om[1, 1]))
        print("      px_per_cell_x=%s  px_per_cell_y=%s"
              % (old.get("px_per_cell_x"), old.get("px_per_cell_y")))
        print("\n  ratio measured/stored: %s"
              % np.round(np.divide(M, om, out=np.full_like(M, np.nan),
                                   where=np.abs(om) > 1e-9), 3).tolist())
        for row in old.get("rows", []):
            if abs(row[2]) < 1e-9 and abs(row[3]) < 1e-9:
                print("  NOTE stored calibration row %r recorded no world movement"
                      % (row[4],))
    except Exception as exc:
        print("could not read calib_grid.json: %s" % exc)

    print("\n  interpretation: a screen offset of (sx,sy) from the character")
    print("  moves it by M @ (sx,sy) world cells.  If M disagrees with the")
    print("  stored matrix, every 'walk to X,Y' decision was computed with the")
    print("  wrong direction, not merely a wrong distance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
