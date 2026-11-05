"""Closed-loop proof: eyes -> brain -> hand.

Spawns its own target window (a red disc on a black canvas at a random spot),
then repeatedly:

    screenshot  ->  find the disc by colour  ->  compute the error
                ->  drive the board (hardware mouse)  ->  click  ->  verify

Nothing real is touched: the target is a window this script creates, so it can
be run without approval and without risking a game or an office app.

The point is to prove the *loop* works end to end:

    eyes   PIL screen grab + numpy colour search   (target found from pixels)
    brain  proportional controller, iterated to convergence
    hand   RP2040 INJECT over the config channel   (real HID reports)
    verify the app's own hit counter

    python loop_demo.py [--trials 8]

Two traps this deliberately avoids, both of which silently cap accuracy:

  * **Window frame offset.**  A normal window's *client* area starts below the
    title bar and inside the border, so a target drawn relative to the window
    geometry lands ~30 px away from where you think it is.  This window is
    borderless fullscreen, so client origin == screen origin.
  * **Unmapped widgets report 0.**  `winfo_rootx()` returns 0 until the window
    is actually mapped, so ask only after `root.update()`.

It also auto-closes after a hard timeout, so a mistake here cannot leave a
black fullscreen window on the user's desk.
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time

import numpy as np
import tkinter as tk
from PIL import ImageGrab

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman  # noqa: E402

MARGIN = 90                   # keep the disc away from the screen edges
DISC_R = 42
BG = "#101014"
DISC = (230, 40, 40)
TOL = dict(r_min=170, g_max=90, b_max=90)

state = {"target": (0, 0), "hits": 0, "misses": 0, "quit": False}
lock = threading.Lock()


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor() -> tuple[int, int]:
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def screen_size() -> tuple[int, int]:
    u = ctypes.windll.user32
    return u.GetSystemMetrics(0), u.GetSystemMetrics(1)


# --------------------------------------------------------------------- eyes
def find_disc(box):
    """Return the disc's screen centre, found purely from pixels."""
    img = ImageGrab.grab()
    arr = np.asarray(img.convert("RGB"))
    ih, iw = arr.shape[:2]
    sw, sh = screen_size()
    sx, sy = sw / iw, sh / ih            # DPI scaling between grab and screen

    x0, y0, x1, y1 = box
    gx0, gy0 = int(x0 / sx), int(y0 / sy)
    gx1, gy1 = int(x1 / sx), int(y1 / sy)
    sub = arr[max(0, gy0):gy1, max(0, gx0):gx1]
    if sub.size == 0:
        return None, 0

    r = sub[:, :, 0].astype(int)
    g = sub[:, :, 1].astype(int)
    b = sub[:, :, 2].astype(int)
    mask = (r > TOL["r_min"]) & (g < TOL["g_max"]) & (b < TOL["b_max"])
    n = int(mask.sum())
    if n < 50:
        return None, n
    ys, xs = np.nonzero(mask)
    return ((xs.mean() + gx0) * sx, (ys.mean() + gy0) * sy), n


# --------------------------------------------------------------------- brain
def approach(m, tx, ty, tolerance=4.0, max_steps=12):
    """P-controller: drive the real cursor onto (tx, ty) using the board."""
    scale = m.pixels_per_count
    errs = []
    for _ in range(max_steps):
        cx, cy = cursor()
        dx, dy = tx - cx, ty - cy
        err = (dx * dx + dy * dy) ** 0.5
        errs.append(err)
        if err <= tolerance:
            break
        # The mapping is linear and exact, so go the whole way; the iteration
        # then cleans up the sub-count quantisation (1 count ~ 2.6 px here).
        m.inject_mouse(dx=round(dx / scale), dy=round(dy / scale))
        time.sleep(0.02)
    cx, cy = cursor()
    return errs, ((tx - cx) ** 2 + (ty - cy) ** 2) ** 0.5


# ---------------------------------------------------------------------- hand
def click(m):
    m.inject_mouse(buttons=1)            # press
    time.sleep(0.05)
    m.inject_mouse(buttons=0)            # release
    time.sleep(0.08)


# ----------------------------------------------------------------------- app
def build_window():
    sw, sh = screen_size()
    root = tk.Tk()
    root.overrideredirect(True)          # no title bar, no border
    root.geometry("%dx%d+0+0" % (sw, sh))
    root.configure(bg=BG)
    root.attributes("-topmost", True)

    canvas = tk.Canvas(root, width=sw, height=sh, bg=BG, highlightthickness=0)
    canvas.pack()
    root.update()                        # MUST map before asking for geometry

    ox, oy = canvas.winfo_rootx(), canvas.winfo_rooty()
    print("target window: %dx%d, client origin (%d,%d)" % (sw, sh, ox, oy))
    if ox or oy:
        print("  note: client origin is not (0,0); using the reported value")

    def new_target():
        tx = ox + MARGIN + int(np.random.rand() * (sw - 2 * MARGIN))
        ty = oy + MARGIN + int(np.random.rand() * (sh - 2 * MARGIN))
        with lock:
            state["target"] = (tx, ty)
        canvas.coords(disc,
                      tx - ox - DISC_R, ty - oy - DISC_R,
                      tx - ox + DISC_R, ty - oy + DISC_R)

    disc = canvas.create_oval(0, 0, 0, 0, fill="#%02x%02x%02x" % DISC, outline="")

    def on_click(ev):
        with lock:
            tx, ty = state["target"]
        hit = ((ev.x_root - tx) ** 2 + (ev.y_root - ty) ** 2) ** 0.5 <= DISC_R
        with lock:
            state["hits" if hit else "misses"] += 1
        new_target()

    canvas.bind("<Button-1>", on_click)
    root.bind("<Escape>", lambda e: root.destroy())
    new_target()
    return root, (ox, oy, ox + sw, oy + sh)


# ---------------------------------------------------------------------- main
def worker(box, trials):
    time.sleep(1.2)                       # let the window appear
    with Middleman().open(want_monitor=False) as m:
        print("firmware inject support:", m.has_inject())
        m.pixels_per_count = m.calibrate()
        print("calibrated: %.3f px per count" % m.pixels_per_count)
        print()
        print("  trial   disc(detected)   start err  steps  final err   click      time")
        print("  ------------------------------------------------------------------------")

        passed = 0
        det_errors = []
        for i in range(1, trials + 1):
            with lock:
                true_t = state["target"]
                hits_before = state["hits"]

            found, npx = find_disc(box)
            if found is None:
                print("  %5d   disc not found (%d px)" % (i, npx))
                continue

            # how far the *vision* was from the truth (the disc has not moved)
            det_errors.append(((found[0] - true_t[0]) ** 2 +
                               (found[1] - true_t[1]) ** 2) ** 0.5)

            cx, cy = cursor()
            start = ((found[0] - cx) ** 2 + (found[1] - cy) ** 2) ** 0.5
            t0 = time.time()
            errs, final = approach(m, found[0], found[1])
            click(m)
            time.sleep(0.15)
            with lock:
                hit = state["hits"] > hits_before
            passed += 1 if hit else 0
            print("  %5d   (%6.0f,%6.0f)  %8.0f  %5d  %9.1f   %-5s  %6.0f ms"
                  % (i, found[0], found[1], start, len(errs), final,
                     "HIT" if hit else "MISS", (time.time() - t0) * 1000))

        print()
        if det_errors:
            print("vision error (found vs true centre): median %.1f px, worst %.1f px"
                  % (float(np.median(det_errors)), max(det_errors)))
        print("clicks landed on the target: %d/%d" % (passed, trials))
        print()
        if passed >= max(1, int(trials * 0.75)):
            print("PASS: the eyes -> brain -> hand loop works end to end.")
        else:
            print("FAIL: the loop did not converge often enough.")

    with lock:
        state["quit"] = True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=8)
    args = ap.parse_args()

    root, box = build_window()

    # Hard safety net: never leave a black fullscreen window behind.
    root.after(int((args.trials * 2.0 + 20) * 1000), root.destroy)

    t = threading.Thread(target=worker, args=(box, args.trials), daemon=True)
    t.start()

    def poll():
        with lock:
            done = state["quit"]
        if done:
            root.destroy()
        else:
            root.after(150, poll)

    root.after(150, poll)
    root.mainloop()
    t.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
