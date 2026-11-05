"""Screen capture with several backends, because one of them will be blind.

**The problem this exists for:** `PIL.ImageGrab` uses GDI `BitBlt`. Any window
rendered on the GPU (game launchers, DirectX/Unity clients, hardware-accelerated
UIs) comes back **uniformly white or black** — no error, just a blank picture.
That silently destroys the "eyes" half of any automation loop.

Backends, in order of preference:

    dxgi    Desktop Duplication (dxcam).  Captures the composited desktop, so it
            sees GPU-rendered and exclusive-fullscreen content.  Fast.
    print   PrintWindow(hwnd, PW_RENDERFULLCONTENT).  Per-window, no dependency,
            works for some GPU windows but not exclusive fullscreen.
    gdi     PIL.ImageGrab.  Universal fallback, blind to GPU surfaces.

`probe_windows()` captures every visible top-level window with every backend and
reports which combination actually yields image content — i.e. "which method can
see this window".  Run it when a target looks blank.

    python screen_grab.py                 # probe all visible windows
    python screen_grab.py --save          # also dump one capture per window
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

import numpy as np

# --------------------------------------------------------------------- GDI
def grab_gdi():
    from PIL import ImageGrab
    return np.asarray(ImageGrab.grab().convert("RGB"))


# -------------------------------------------------------------------- DXGI
_dxcam = {"cam": None, "err": None}


def _dxgi_camera():
    if _dxcam["cam"] is not None:
        return _dxcam["cam"]
    if _dxcam["err"]:
        return None
    try:
        import dxcam
        cam = dxcam.create(output_idx=0, output_color="RGB")
        # video_mode makes get_latest_frame() always return a frame, instead of
        # None when the screen has not changed since the last grab.
        cam.start(target_fps=30, video_mode=True)
        time.sleep(0.25)
        _dxcam["cam"] = cam
    except Exception as exc:
        _dxcam["err"] = repr(exc)
    return _dxcam["cam"]


def grab_dxgi():
    cam = _dxgi_camera()
    if cam is None:
        raise RuntimeError("dxcam unavailable: %s" % _dxcam["err"])
    frame = cam.get_latest_frame()
    if frame is None:
        raise RuntimeError("dxcam returned no frame")
    return np.ascontiguousarray(frame)


# ------------------------------------------------------------- PrintWindow
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

PW_RENDERFULLCONTENT = 0x00000002
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def window_rect(hwnd):
    """Screen rect of a window, using the DWM frame bounds (no shadow padding)."""
    r = wt.RECT()
    if dwmapi.DwmGetWindowAttribute(wt.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(r), ctypes.sizeof(r)) != 0:
        if not user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r)):
            return None
    return (r.left, r.top, r.right, r.bottom)


def grab_printwindow(hwnd):
    l, t, r, b = window_rect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise RuntimeError("empty window rect")
    hdc = user32.GetWindowDC(wt.HWND(hwnd))
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(wt.HWND(hwnd), mem, PW_RENDERFULLCONTENT)
    if not ok:
        user32.PrintWindow(wt.HWND(hwnd), mem, 0)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h          # top-down
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(wt.HWND(hwnd), hdc)

    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, 2::-1]
    return np.ascontiguousarray(arr)


# ------------------------------------------------------------------ helpers
def content_score(img) -> tuple[float, float]:
    """(mean, std) of a capture.  std == 0 means a blank surface."""
    if img is None:
        return -1.0, -1.0
    a = img.astype(np.float32)
    return float(a.mean()), float(a.std())


def visible_windows():
    """Top-level windows with a title, that are visible and not tiny."""
    out = []
    EnumProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        r = window_rect(hwnd)
        if not r:
            return True
        l, t, rr, b = r
        if (rr - l) < 200 or (b - t) < 150:
            return True
        out.append((hwnd, buf.value, r))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def probe(save_dir: str | None = None) -> None:
    wins = visible_windows()
    print("visible top-level windows: %d" % len(wins))
    try:
        full_dxgi = grab_dxgi()
        m, s = content_score(full_dxgi)
        print("dxgi full desktop : %dx%d  mean=%.1f std=%.1f" % (
            full_dxgi.shape[1], full_dxgi.shape[0], m, s))
    except Exception as exc:
        print("dxgi full desktop : FAILED (%s)" % exc)
        full_dxgi = None

    try:
        full_gdi = grab_gdi()
        m, s = content_score(full_gdi)
        print("gdi  full desktop : %dx%d  mean=%.1f std=%.1f" % (
            full_gdi.shape[1], full_gdi.shape[0], m, s))
    except Exception as exc:
        print("gdi  full desktop : FAILED (%s)" % exc)
        full_gdi = None

    print()
    print("  %-34s %-16s %-16s %-16s" % ("window", "gdi(std)", "dxgi(std)", "print(std)"))
    print("  " + "-" * 86)
    for hwnd, title, rect in wins:
        l, t, r, b = rect
        row = []
        crops = {}
        for name, full in (("gdi", full_gdi), ("dxgi", full_dxgi)):
            if full is None:
                row.append("n/a")
                continue
            try:
                h, w = full.shape[:2]
                sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
                sx, sy = w / sw, h / sh
                c = full[max(0, int(t * sy)):int(b * sy), max(0, int(l * sx)):int(r * sx)]
                _, s = content_score(c)
                crops[name] = c
                row.append("%.1f" % s)
            except Exception:
                row.append("err")
        try:
            pw = grab_printwindow(hwnd)
            _, s = content_score(pw)
            crops["print"] = pw
            row.append("%.1f" % s)
        except Exception as exc:
            row.append("err")
        flag = ""
        if row and row[0] not in ("n/a", "err") and float(row[0]) < 1.0:
            flag = "   <- GDI BLIND"
        print("  %-34s %-16s %-16s %-16s%s"
              % (title[:34], row[0], row[1], row[2], flag))

        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            safe = "".join(c if c.isalnum() else "_" for c in title)[:30]
            for name, c in crops.items():
                try:
                    from PIL import Image
                    Image.fromarray(c).save(os.path.join(save_dir, "%s_%s.png" % (safe, name)))
                except Exception:
                    pass
    print()
    print("std near 0  ->  that backend sees nothing but a blank surface")
    print("If GDI is blind on your target, use dxgi (dxcam) or run the target windowed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    probe("D:/rp2040-build/grabs" if args.save else None)
