"""Is interface 0's IN endpoint dead, or just the mouse path?

Injects a keyboard key ('A', usage 0x00070004) through the board and watches a
WH_KEYBOARD_LL hook.  If the key arrives, EP 0x81 works and only the mouse
report is being dropped; if nothing arrives either, the whole interface is not
being polled.

    python mcu_kb_test.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcu_hid import Middleman  # noqa: E402

WH_KEYBOARD_LL = 13
WM_QUIT = 0x0012
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
events: list[tuple[int, int]] = []
_ready = threading.Event()
_tid = 0


def _proc(nCode, wParam, lParam):
    try:
        if nCode >= 0:
            info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            events.append((wParam, info.vkCode))
        return 1 if nCode >= 0 else 0
    except Exception:
        return 1


_ref = HOOKPROC(_proc)


def _pump():
    global _tid
    _tid = ctypes.windll.kernel32.GetCurrentThreadId()
    _ready.h = ctypes.windll.user32.SetWindowsHookExW(WH_KEYBOARD_LL, _ref, None, 0)
    _ready.set()
    msg = wt.MSG()
    while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main() -> int:
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    _ready.wait(3)
    print("keyboard hook installed:", bool(getattr(_ready, "h", None)))
    time.sleep(0.3)

    with Middleman().open(want_monitor=False) as m:
        print("inject support:", m.has_inject())
        events.clear()
        print("injecting key A (0x00070004) down+up ...")
        m.inject_key([(0x0004, True)])
        time.sleep(0.25)
        m.inject_key([(0x0004, False)])
        time.sleep(0.6)
        print("keyboard events seen:", len(events), events[:6])

    ctypes.windll.user32.PostThreadMessageW(_tid, WM_QUIT, 0, 0)
    print()
    if events:
        print("=> EP 0x81 WORKS -- keyboard reached Windows, only the mouse report is dropped")
    else:
        print("=> nothing reached Windows on interface 0 at all")
    return 0 if events else 1


if __name__ == "__main__":
    sys.exit(main())
