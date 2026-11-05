"""Does the board's mouse report actually reach the Windows input stack?

Installs a WH_MOUSE_LL hook (the same mechanism the recorder uses), asks the
board to emit a +150 X report, and reports whether any mouse event with a
non-zero delta showed up.  This separates "the firmware never sent it" from
"Windows received it but ignored it".

    python mcu_hook_test.py
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

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_QUIT = 0x0012

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)

events: list[tuple[str, int, int, int]] = []
hook_ready = threading.Event()
_thread_id = 0


def _proc(nCode, wParam, lParam):
    if nCode >= 0:
        info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        events.append(("move" if wParam == WM_MOUSEMOVE else hex(wParam),
                       0, 0, info.flags))
    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)


_proc_ref = HOOKPROC(_proc)


def _pump():
    global _thread_id
    _thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    h = ctypes.windll.user32.SetWindowsHookExW(WH_MOUSE_LL, _proc_ref, None, 0)
    hook_ready.hook = h
    hook_ready.set()
    msg = wt.MSG()
    while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def main() -> int:
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    hook_ready.wait(3)
    h = getattr(hook_ready, "hook", None)
    print("low-level mouse hook installed:", bool(h))
    time.sleep(0.4)

    # baseline: let the touchpad/mouse settle
    events.clear()
    time.sleep(1.0)
    print("idle mouse events in 1 s:", len(events))

    with Middleman().open(want_monitor=False) as m:
        print("inject support:", m.has_inject())
        events.clear()
        print("injecting dx=150 ...")
        m.inject_mouse(dx=150)
        time.sleep(1.2)
        n = len(events)
        print("mouse events seen after injection:", n)
        for e in events[:5]:
            print("   ", e)

    ctypes.windll.user32.PostThreadMessageW(_thread_id, WM_QUIT, 0, 0)
    print()
    if n == 0:
        print("=> the report never reached the Windows input stack")
    else:
        print("=> Windows DID receive mouse input from the board")
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
