"""coord_diag -- measure the coordinate reader against ground truth, fairly.

The rule that makes this a real measurement: **one frame, read once, shown once.**

Previous attempts compared a live reading against a screenshot taken minutes
earlier - by which time the character had walked, so the numbers differed and the
reader looked broken when it was not.  And the reader itself calls
`vc.grab_client()` internally, so even "show the frame, then read" is two frames
unless you pin it.

So: grab ONE frame, monkeypatch `vc.grab_client` to keep handing that same frame
back, then both print it as ASCII art (human-readable ground truth) and run the
real reader against it.

    python coord_diag.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import coord_ocr as co          # noqa: E402
import vision_color as vc       # noqa: E402


def art(bm):
    return "\n".join(
        "".join("#" if bm.getpixel((x, y)) >= 128 else "." for x in range(bm.width))
        for y in range(bm.height))


def main() -> int:
    cfg = json.load(open(co.CELL_CFG, encoding="utf-8"))
    lib = json.load(open(co.TPL, encoding="utf-8"))
    x0, pitch, th = cfg["X0"], cfg["PITCH"], cfg["TH"]
    y0, y1 = cfg["Y0"], cfg["Y1"]
    print("cfg :", cfg, "  lib: %d templates" % len(lib))

    # ---- ONE frame, pinned so every later call sees the same pixels ---------
    h = vc.find_window()[0]
    arr = vc.grab_client(h)[0]
    vc.grab_client = lambda hwnd=None, retries=4: (arr, None)     # pin it
    print("pinned one frame: %s" % (arr.shape,))

    im = Image.fromarray(arr).convert("L")

    # ---- the band, wide enough to include the map name ---------------------
    band = im.crop((0, y0, 260, y1))
    print("\n=== band y=%d..%d, x=0..260 ===" % (y0, y1))
    print("    " + "".join(str(i // 100 % 10) for i in range(band.width)))
    print("    " + "".join(str(i // 10 % 10) for i in range(band.width)))
    print("    " + "".join(str(i % 10) for i in range(band.width)))
    print(art(band))

    # ---- per fixed cell ----------------------------------------------------
    print("\n=== cells (x0=%d pitch=%d) ===" % (x0, pitch))
    for i in range(9):
        cx0 = x0 + i * pitch
        bm, size = co._cell_key(band, cx0, cx0 + pitch, th)
        if bm is None:
            print(" cell %d  x=%d..%d  (empty)" % (i, cx0, cx0 + pitch))
            continue
        k = co.glyph_key(bm)
        exact = lib.get("%dx%d:%s" % (k["w"], k["h"], k["bits"]))
        rows = art(bm).split("\n")
        print(" cell %d  x=%d..%d  size=%s  exact=%r" % (i, cx0, cx0 + pitch, size, exact))
        for r in rows:
            print("        " + r)

    # ---- and what the real reader says about the SAME pinned frame ---------
    print("\n=== reader on the SAME pinned frame ===")
    print("  read_coord_cells ->", co.read_coord_cells(verbose=True))
    print("  read_coord       ->", co.read_coord())
    print()

    # ---- the full text height, NOT clipped to the configured 5 rows -------
    # read_coord_cells() finds an 8-row text band and then uses only its TOP 5
    # rows against a 5-row template library.  If the distinguishing strokes of
    # a glyph live in the bottom rows, the reader can never tell them apart.
    print("=== TALL band y=%d..%d (8 rows, what the detector actually found) ==="
          % (y0, y0 + 8))
    tall = im.crop((0, y0, 260, y0 + 8))
    print("    " + "".join(str(i // 10 % 10) for i in range(tall.width)))
    print("    " + "".join(str(i % 10) for i in range(tall.width)))
    print(art(tall))
    print("=== TALL cells with the same x grid ===")
    for i in range(9):
        cx0 = x0 + i * pitch
        bm, size = co._cell_key(tall, cx0, cx0 + pitch, th)
        if bm is None:
            print(" cell %d (empty)" % i)
            continue
        print(" cell %d  size=%s" % (i, size))
        for r in art(bm).split("\n"):
            print("        " + r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
