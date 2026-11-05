"""coord_capture -- grab a dataset of the game's coordinate readout.

`coord_ocr.py` reads `<地图名> <X>:<Y>` from the bottom-left of the client area,
but it does so with hand-tuned pixel constants and 5-row glyph templates, and
its own comments record four separate times that broke.  You cannot fix that by
staring at one screenshot; you need many samples across many values.

So this captures, cheaply:

    full/NNNN.jpg     the whole client area, occasionally (context)
    strip/NNNN.png    a GENEROUS bottom-left band, always
    strips.png        every strip stacked into one tall montage, scaled up

The montage is the point: 40 coordinate readouts in a single image, so they can
be read off and turned into ground truth in one pass instead of forty.

Note the band is deliberately **generous** (x 0..340, bottom 100 px).  Working out
where the text actually sits is part of the job - do not bake the old constants
into the capture or you inherit the old assumptions.

    python coord_capture.py --seconds 60 --out coordshots
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", default="coordshots")
    ap.add_argument("--interval", type=float, default=0.35)
    ap.add_argument("--window", default="i1fOFW", help="substring of the window title")
    ap.add_argument("--auto", action="store_true",
                    help="pick the largest non-system window (use this: passing a "
                         "Chinese title through the shell gets mangled)")
    ap.add_argument("--band-x", type=int, default=340, help="width of the bottom-left band")
    ap.add_argument("--band-h", type=int, default=100, help="height of the band")
    ap.add_argument("--full-every", type=int, default=8, help="save a full frame every N shots")
    ap.add_argument("--client-only", action="store_true", default=True)
    args = ap.parse_args()

    from screen_grab import grab_gdi, visible_windows, window_rect

    hwnd = title = rect = None
    if args.auto:
        SKIP = ("program manager", "microsoft text input", "deepseek harness",
                "设置", "电影和电视", "豆包", "obsidian", "hermes")
        best = None
        for h, t, r in visible_windows():
            if any(s in t.lower() for s in SKIP):
                continue
            area = (r[2] - max(0, r[0])) * (r[3] - max(0, r[1]))
            if best is None or area > best[0]:
                best = (area, h, t, r)
        if best:
            _, hwnd, title, rect = best
    if hwnd is None:
        for h, t, r in visible_windows():
            if args.window.lower() in t.lower():
                hwnd, title, rect = h, t, r
                break
    if hwnd is None:
        print("game window not found")
        print("open windows (pass --auto to pick the largest):")
        for i, (_, t, r) in enumerate(visible_windows()):
            print("   [%d] %r  %dx%d" % (i, t, r[2] - r[0], r[3] - r[1]))
        return 1

    l, t, r, b = rect
    L, T = max(0, l), max(0, t)
    cw, ch = r - L, b - T
    print("window %r  rect=%s  client≈%dx%d" % (title, rect, cw, ch))

    full_dir = os.path.join(args.out, "full")
    strip_dir = os.path.join(args.out, "strip")
    os.makedirs(full_dir, exist_ok=True)
    os.makedirs(strip_dir, exist_ok=True)

    strips = []
    t0 = time.time()
    n = 0
    while time.time() - t0 < args.seconds:
        try:
            screen = grab_gdi()[T:b, L:r]
        except Exception as exc:
            print("  grab failed: %s" % exc)
            time.sleep(0.2)
            continue
        n += 1
        img = Image.fromarray(screen)

        # generous bottom-left band
        y0 = max(0, img.height - args.band_h)
        band = img.crop((0, y0, min(args.band_x, img.width), img.height))
        name = "s%04d.png" % n
        band.save(os.path.join(strip_dir, name))
        strips.append((n, name, round(time.time() - t0, 2), band))

        if args.full_every and n % args.full_every == 0:
            img.save(os.path.join(full_dir, "f%04d.jpg" % n), quality=85)

        if n % 10 == 0:
            print("  %4d shots  %.1fs" % (n, time.time() - t0))
        time.sleep(args.interval)

    print("captured %d strips, %d full frames in %.1fs"
          % (n, n // max(1, args.full_every), time.time() - t0))

    # ---------------------------------------------------------- strip montage
    if strips:
        scale = 3
        w = max(s.width for _, _, _, s in strips) * scale
        h = sum(s.height * scale + 2 for _, _, _, s in strips)
        sheet = Image.new("RGB", (w + 70, h), (18, 18, 22))
        d = ImageDraw.Draw(sheet)
        try:
            font = ImageFont.truetype("consola.ttf", 13)
        except Exception:
            font = ImageFont.load_default()
        y = 0
        for idx, name, ts, s in strips:
            d.text((4, y + 8), "%3d" % idx, fill=(255, 220, 80), font=font)
            big = s.resize((s.width * scale, s.height * scale), Image.NEAREST)
            sheet.paste(big, (64, y))
            y += s.height * scale + 2
        out = os.path.join(args.out, "strips.png")
        sheet.save(out)
        print("montage ->", out, sheet.size)

    print()
    print("next: read the montage, write down the true X:Y for each index, and")
    print("use that as ground truth to score any coordinate reader.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
