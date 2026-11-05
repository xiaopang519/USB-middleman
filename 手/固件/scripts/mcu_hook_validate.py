"""Validate the WH_MOUSE_LL hook by generating our own input with SendInput.

If the hook does not see SendInput's own events, every "nothing arrived"
conclusion from the hook tests is worthless.

    python mcu_hook_validate.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_QUIT = 0x0012
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
seen = {"move": 0, "flags": set()}
_tid = 0
_ready = threading.Event()


def _proc(nCode, wParam, lParam):
    if nCode >= 0 and wParam == WM_MOUSEMOVE:
        info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        seen["move"] += 1
        if len(seen["flags"]) < 4:
            seen["flags"].add(info.flags)
    return ctypes.windll.user32.CallNextHookEx(None, nCode, wParam, lParam)


_ref = HOOKPROC(_proc)


def _pump():
    global _tid
    _tid = ctypes.windll.kernel32.GetCurrentThreadId()
    _ready.h = ctypes.windll.user32.SetWindowsHookExW(WH_MOUSE_LL, _ref, None, 0)
    _ready.set()
    msg = wt.MSG()
    while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
        ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))


def send_move(dx: int, dy: int) -> None:
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=MOUSEEVENTF_MOVE,
                        time=0, dwExtraInfo=0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def main() -> int:
    t = threading.Thread(target=_pump, daemon=True)
    t.start()
    _ready.wait(3)
    print("hook installed:", bool(getattr(_ready, "h", None)))
    time.sleep(0.4)

    p = wt.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    print("cursor before:", (p.x, p.y))

    seen["move"] = 0
    send_move(60, 40)
    time.sleep(0.5)
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    print("cursor after SendInput:", (p.x, p.y))
    print("hook events from SendInput:", seen["move"], "flags:", seen["flags"])

    if seen["move"]:
        print("\n=> the hook WORKS")
    else:
        print("\n=> the hook is BLIND -- earlier 'nothing arrived' results are void")
    ctypes.windll.user32.PostThreadMessageW(_tid, WM_QUIT, 0, 0)
    return 0 if seen["move"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
