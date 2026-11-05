"""calib_solve -- fit the screen<->world mapping from recorded walk data.

Reads the JSON that `calib_fit.py` wrote and solves, by least squares:

    world_delta = M @ cursor_offset + k          where k = -M @ char_screen

This is LINEAR in the six unknowns (four for M, two for k), and it has a very
useful side effect: since k = -M @ char_screen,

    char_screen = -inv(M) @ k

so the same fit also recovers **where the character is drawn on screen** - which
nothing in the current code actually knows; it aims relative to the client centre
and hopes.

Blocked directions are the main danger: if a wall stops the character, the
measured delta is a projection of the true motion and biases M.  So each
direction is summarised by its MEDIAN and reported with a consistency score, and
`--min-move` drops directions that barely moved.

    python calib_solve.py                       # reads calib_measured.json
    python calib_solve.py --min-move 2.5
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="calib_measured.json")
    ap.add_argument("--min-move", type=float, default=2.5,
                    help="drop a direction whose median move is shorter than this "
                         "(likely blocked by terrain)")
    args = ap.parse_args()

    if not os.path.isfile(args.data):
        print("no data file %s - run calib_fit.py first" % args.data)
        return 1
    data = json.load(open(args.data, encoding="utf-8"))
    if not data:
        print("data file is empty")
        return 1

    # ---- per-direction median, plus how consistent the repeats were ----------
    by_dir = {}
    for r in data:
        by_dir.setdefault(r["dir"], []).append(r)

    print("  dir   n   median dX,dY    spread   verdict")
    print("  " + "-" * 52)
    usable = []
    for name, rows in by_dir.items():
        dx = np.array([r["dX"] for r in rows], dtype=float)
        dy = np.array([r["dY"] for r in rows], dtype=float)
        mx, my = float(np.median(dx)), float(np.median(dy))
        mag = (mx * mx + my * my) ** 0.5
        spread = float(np.mean(np.hypot(dx - mx, dy - my)))
        ok = mag >= args.min_move
        print("  %-4s %2d   %+6.2f,%+6.2f   %5.2f    %s"
              % (name, len(rows), mx, my, spread,
                 "use" if ok else "DROPPED (blocked?)"))
        if ok:
            usable.append((rows[0]["ux"], rows[0]["uy"], rows[0]["px"], mx, my))

    if len(usable) < 3:
        print("\nonly %d usable directions - the character is probably in a")
        print("corridor.  Move it to open ground and re-run calib_fit.py.")
        return 1

    # ---- least squares: world = M @ (ux*d, uy*d) + k ------------------------
    A, b = [], []
    for ux, uy, d, mx, my in usable:
        sx, sy = ux * d, uy * d
        A.append([sx, sy, 0, 0, 1, 0]); b.append(mx)
        A.append([0, 0, sx, sy, 0, 1]); b.append(my)
    A = np.array(A, dtype=float)
    b = np.array(b, dtype=float)
    sol, res, rank, sv = np.linalg.lstsq(A, b, rcond=None)
    m00, m01, m10, m11, kx, ky = sol
    M = np.array([[m00, m01], [m10, m11]])
    k = np.array([kx, ky])

    print("\n  fitted M (world cells per screen pixel):")
    print("      dX = %+.6f * sx  %+.6f * sy" % (m00, m01))
    print("      dY = %+.6f * sx  %+.6f * sy" % (m10, m11))
    print("      rank=%d  residual=%.3f" % (rank, float(res[0]) if len(res) else -1))

    det = float(np.linalg.det(M))
    if abs(det) < 1e-12:
        print("  M is singular; cannot recover the character position")
        return 1
    char_screen = -np.linalg.solve(M, k)
    print("\n  implied character screen position (client px): (%.0f, %.0f)"
          % (char_screen[0], char_screen[1]))
    print("      client centre was (%d, %d)" % (640, 525))
    print("      offset from centre: (%+.0f, %+.0f)"
          % (char_screen[0] - 640, char_screen[1] - 525))

    # ---- residual per direction -------------------------------------------
    print("\n  per-direction fit error (cells):")
    worst = 0.0
    for ux, uy, d, mx, my in usable:
        pred = M @ np.array([ux * d, uy * d]) + k
        err = float(np.hypot(pred[0] - mx, pred[1] - my))
        worst = max(worst, err)
        print("      (%+d,%+d)  measured (%+6.2f,%+6.2f)  predicted (%+6.2f,%+6.2f)  err %.2f"
              % (ux, uy, mx, my, pred[0], pred[1], err))
    print("      worst error: %.2f cells" % worst)

    # ---- versus the stored matrix -----------------------------------------
    try:
        old = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "calib_grid.json"), encoding="utf-8"))
        om = np.array(old["matrix"], dtype=float)
        print("\n  stored calib_grid.json:")
        print("      dX = %+.6f * sx  %+.6f * sy" % (om[0, 0], om[0, 1]))
        print("      dY = %+.6f * sx  %+.6f * sy" % (om[1, 0], om[1, 1]))
        print("      -> it predicts world dX for a pure horizontal cursor move of")
        print("         %+.2f cells per 100 px, and world dY of %+.2f per 100 px."
              % (om[0, 0] * 100, om[1, 0] * 100))
        print("      measured:")
        print("         %+.2f cells per 100 px horizontally, %+.2f vertically."
              % (m00 * 100, m10 * 100))
        print("\n      KEY CHECK - does the stored matrix have off-diagonal zeros")
        print("      where the measurement says they are large?  If so, a pure")
        print("      horizontal cursor move was believed to change ONLY world X,")
        print("      while it actually changes X and Y together: every 'walk to")
        print("      X,Y' decision then steers the character the wrong way.")
        print("         stored off-diagonals: %+.6f, %+.6f" % (om[0, 1], om[1, 0]))
        print("         measured off-diagonals: %+.6f, %+.6f" % (m01, m10))
    except Exception as exc:
        print("could not read calib_grid.json: %s" % exc)

    out = os.path.join(os.path.dirname(os.path.abspath(args.data)), "calib_new.json")
    json.dump({"matrix": M.tolist(), "k": k.tolist(),
               "char_screen": char_screen.tolist(),
               "px_per_cell_x": float(1 / max(1e-9, abs(m00))),
               "px_per_cell_y": float(1 / max(1e-9, abs(m11))),
               "source": args.data, "usable_dirs": len(usable),
               "worst_fit_error_cells": worst}, open(out, "w"), indent=1)
    print("\n  wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
