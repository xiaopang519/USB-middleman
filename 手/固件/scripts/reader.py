"""reader -- one place to ask "where is the character?", preferring the v2 reader.

`coord_ocr.read_coord` pins the digit start at a fixed x and returns None as soon
as the map name changes length (that is exactly what happened at 盟重省: the
digits moved 36 px left and every read failed).  `coord_v2` anchors on the colon
instead, so the map name stops mattering.

This shim keeps a single template library loaded and falls back to the old reader
if the new one cannot see a band at all - during a loading screen, say - so tools
keep working while the replacement is proven.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import vision_color as vc          # noqa: E402
import coord_v2                    # noqa: E402

_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        _LIB = coord_v2.Lib()
    return _LIB


def read_coord(hwnd=None, verbose=False):
    """Returns (x, y) or None.  Never raises on a loading screen."""
    try:
        arr = vc.grab_client(hwnd or vc.find_window()[0])[0]
    except Exception:
        return None
    try:
        r = coord_v2.read_coord(arr, _lib(), verbose=verbose)
        if r:
            return r
    except Exception:
        pass
    try:
        import coord_ocr
        return coord_ocr.read_coord(hwnd=hwnd)
    except Exception:
        return None


def read_coord_arr(arr, verbose=False):
    """Same, but on an image you already grabbed - for offline scoring."""
    try:
        return coord_v2.read_coord(arr, _lib(), verbose=verbose)
    except Exception:
        return None


if __name__ == "__main__":
    for i in range(5):
        print(read_coord())
