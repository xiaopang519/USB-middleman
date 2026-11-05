"""coord_v2 -- read `<mapname> <X>:<Y>` with the COLON as the anchor.

The failure being fixed
-----------------------
`ocr_cell.json` pins the digit start at `X0: 98`, which is only correct for one
map-name length:

    沃玛寺庙 一层 385:332     digits begin ~98     ok
    盟重省 234:378            digits begin ~62     X0=98 lands on the final '8'
                                                  -> one glyph + eight empties -> None

The fallback in `read_coord` (segment the whole strip, look for ':') also fails,
because that strip is 11 rows tall so the glyphs come out 8 rows high while the
template library holds 5-row glyphs, and `_match_glyph` rejects anything whose
size differs by more than 1 - including the ':' it is searching for.

Why the colon, and not segmentation or a fixed X
------------------------------------------------
* A fixed X breaks whenever the map name changes length.
* Plain segmentation breaks the other way: the game font has a 1 px gap between
  digits, so any merge gap big enough to keep a glyph whole also fuses adjacent
  digits ("234" came out as one 17 px blob).

The colon sidesteps both.  It is the only glyph 1-2 px wide, so it is easy to
find, and the font is a fixed 6 px pitch, so every digit sits on the grid
`colon_center +/- 6k`.  The map name's length stops mattering entirely.

    python coord_v2.py --test        # print the detected band, colon and cells
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vision_color as vc          # noqa: E402

TPL = os.path.join(HERE, "ocr_templates.json")
TH = 205
PITCH = 6


def art(bm):
    return "\n".join("".join("#" if bm.getpixel((x, y)) >= 128 else "."
                             for x in range(bm.width)) for y in range(bm.height))


# --------------------------------------------------------------- band finding
def find_band(gray, y_lo=1000, y_hi=1022, x_hi=200, th=TH, min_ink=8):
    """The coordinate line: the lowest run of text rows in the bottom-left."""
    sub = gray[y_lo:y_hi, 0:x_hi]
    ink = (sub >= th).sum(axis=1)
    rows = [y for y in range(len(ink)) if ink[y] >= min_ink]
    if not rows:
        return None
    groups, g0, prev = [], rows[0], rows[0]
    for y in rows[1:]:
        if y - prev <= 2:
            prev = y
        else:
            groups.append((g0, prev))
            g0 = prev = y
    groups.append((g0, prev))
    groups = [g for g in groups if g[1] - g[0] >= 4]
    if not groups:
        return None
    bt, bb = max(groups, key=lambda g: g[0])
    return y_lo + bt, y_lo + bb + 1


# ------------------------------------------------------------------- matching
class Lib:
    def __init__(self, path=TPL):
        self.items = []
        for key, ch in json.load(open(path, encoding="utf-8")).items():
            wh, bits = key.split(":", 1)
            ww, hh = map(int, wh.split("x"))
            self.items.append((ch, Image.frombytes("L", (ww, hh),
                                                   bytes.fromhex(bits))))

    def match(self, bm, top_rows=5):
        """The library holds 5-row glyphs, so compare the TOP rows only.

        Matching an 8-row glyph directly rejects every template on the size
        filter - which is exactly how the old fallback silently found no ':'.

        The horizontal box must then be tightened AGAIN within those top rows.
        A '1' is the reason: its full 8-row bounding box is 3 px wide because of
        the base serif on the bottom row, while the upper five rows are only 2 px
        wide.  Cropping 5 rows out of a 3 px box therefore leaves the ink
        off-centre, one pixel away from the template, and the distance to '3'
        comes out smaller - so every '1' in a coordinate was read as a '3'
        (210 -> 230, 211 -> 233, 571 -> 573), which looked exactly like the
        character teleporting 20 cells.
        """
        top = bm.crop((0, 0, bm.width, min(top_rows, bm.height)))
        a = np.asarray(top, dtype=np.int16)
        cols = np.nonzero((a >= 128).any(axis=0))[0]
        if len(cols) == 0:
            return None, 1e9
        top = top.crop((int(cols[0]), 0, int(cols[-1]) + 1, top.height))

        best, bch = 1e9, None
        for ch, t in self.items:
            tw = t.crop((0, 0, t.width, min(top_rows, t.height)))
            ta = np.asarray(tw, dtype=np.int16)
            tcols = np.nonzero((ta >= 128).any(axis=0))[0]
            if len(tcols) == 0:
                continue
            tw = tw.crop((int(tcols[0]), 0, int(tcols[-1]) + 1, tw.height))
            if abs(tw.width - top.width) > 2:
                continue
            x = np.asarray(tw.resize((6, top_rows), Image.NEAREST), dtype=np.int16)
            y = np.asarray(top.resize((6, top_rows), Image.NEAREST), dtype=np.int16)
            d = float(np.abs(x - y).mean())
            if d < best:
                best, bch = d, ch
        return bch, best


# ------------------------------------------------------------------- the read
def read_coord(arr, lib, verbose=False):
    gray = np.asarray(Image.fromarray(arr).convert("L"))
    band = find_band(gray)
    if band is None:
        if verbose:
            print("no text band found")
        return None
    y0, y1 = band
    if verbose:
        print("band y=%d..%d" % (y0, y1))

    ink = (gray[y0:y1, 0:300] >= TH)

    # column groups; gap of 1 keeps glyphs separate (the font's inter-digit gap
    # is exactly 1 px, which is why any larger merge fuses digits together)
    cols = [x for x in range(ink.shape[1]) if int(ink[:, x].sum()) > 0]
    groups, s, prev = [], None, None
    for x in cols:
        if s is None:
            s = prev = x
        elif x == prev + 1:
            prev = x
        else:
            groups.append((s, prev))
            s = prev = x
    if s is not None:
        groups.append((s, prev))
    groups = [g for g in groups if g[0] >= 8]          # skip the frame border

    if verbose:
        print("column groups:", [(a, b, b - a + 1) for a, b in groups])

    # the colon: narrow, and with a neighbour close enough to be a digit cell
    cand = [g for g in groups if (g[1] - g[0] + 1) <= 2]
    if not cand:
        if verbose:
            print("no narrow glyph found - cannot anchor")
        return None
    # prefer one that has ink within one pitch on both sides
    def has_neighbour(g, step):
        x = g[0] + step
        return any(a <= x <= b for a, b in groups)
    colon = None
    for g in cand:
        if has_neighbour(g, -PITCH) or has_neighbour(g, -PITCH + 1) or \
           has_neighbour(g, PITCH) or has_neighbour(g, PITCH - 1):
            colon = g
            break
    colon = colon or cand[0]
    xc = (colon[0] + colon[1]) / 2.0
    if verbose:
        print("colon at x=%d..%d  centre=%.1f" % (colon[0], colon[1], xc))

    def cell_at(center):
        x0 = int(round(center - PITCH / 2.0))
        x1 = x0 + PITCH
        sub = gray[y0:y1, max(0, x0):x1]
        if sub.size == 0:
            return None, (x0, x1)
        on = sub >= TH
        rows = np.nonzero(on.any(axis=1))[0]
        ccols = np.nonzero(on.any(axis=0))[0]
        if len(rows) == 0 or len(ccols) == 0:
            return None, (x0, x1)
        bm = Image.fromarray(
            np.where(on[rows[0]:rows[-1] + 1, ccols[0]:ccols[-1] + 1], 255, 0)
            .astype(np.uint8), "L")
        return bm, (x0, x1)

    left, right = [], []
    for k in range(1, 4):
        bm, box = cell_at(xc - PITCH * k)
        if bm is None:
            break
        ch, d = lib.match(bm)
        if verbose:
            print("  X cell k=%d box=%s -> %r (d=%.1f)" % (k, box, ch, d))
        if ch is None or not ch.isdigit():
            break
        left.insert(0, ch)
    for k in range(1, 4):
        bm, box = cell_at(xc + PITCH * k)
        if bm is None:
            break
        ch, d = lib.match(bm)
        if verbose:
            print("  Y cell k=%d box=%s -> %r (d=%.1f)" % (k, box, ch, d))
        if ch is None or not ch.isdigit():
            break
        right.append(ch)

    if not left or not right:
        if verbose:
            print("incomplete: left=%s right=%s" % (left, right))
        return None
    return int("".join(left)), int("".join(right))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()
    lib = Lib()
    h = vc.find_window()[0]
    for i in range(args.repeat):
        arr = vc.grab_client(h)[0]
        res = read_coord(arr, lib, verbose=args.test)
        print("coord_v2 ->", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
