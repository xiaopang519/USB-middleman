# -*- coding: utf-8 -*-
"""cursor_server.py —— 在**会话 1**（玩家桌面）里只读光标，本机回环 8311。

为什么必须单独一个进程：
    DshHand 是 SYSTEM 服务，跑在会话 0。会话 0 的 GetCursorPos 返回的是
    服务自己那个桌面的光标，**不是玩家屏幕上的光标**。所以读光标这件事
    必须发生在会话 1 —— 由代理用 launch_in_session 把这个小服务投进去。

只读说明（作者 2026-10-26 的底线）：
    只调 GetCursorPos / GetAsyncKeyState，**不截屏、不读目标程序内存、不枚举目标程序窗口**。
    这两个调用任何普通程序（输入法、屏幕阅读器）都在用，没有可被识别的痕迹。

协议：一行 `q` → 回一行
    OK x=1234 y=567 l=0 r=0 m=0
"""
import ctypes
import socket
import socketserver
import sys
import time
from ctypes import wintypes


class P(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


u = ctypes.WinDLL("user32", use_last_error=True)
u.GetCursorPos.argtypes = [ctypes.POINTER(P)]
u.GetCursorPos.restype = wintypes.BOOL
u.GetAsyncKeyState.argtypes = [ctypes.c_int]
u.GetAsyncKeyState.restype = ctypes.c_short
u.GetForegroundWindow.restype = wintypes.HWND
u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

# ★ 必须声明 DPI 感知：B 是 250% 缩放，不声明会报逻辑坐标(1280x720)，
#   而采集画面是 1920x1080、桌面物理是 3200x1800 —— 三套坐标必须统一到物理像素。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

VK = {"l": 0x01, "r": 0x02, "m": 0x04}


def 读():
    p = P()
    ok = u.GetCursorPos(ctypes.byref(p))
    if not ok:
        return "ERR GetCursorPos"
    btn = {k: 1 if (u.GetAsyncKeyState(v) & 0x8000) else 0 for k, v in VK.items()}
    return "OK x=%d y=%d l=%d r=%d m=%d" % (p.x, p.y, btn["l"], btn["r"], btn["m"])


def 前台() -> str:
    """会话 1 的前台窗口标题。

    ★ 按键只会送给前台窗口。目标程序不在前台时，F2/S 会打进别的程序（或目标程序自己的输入框），
      而这种失败**完全静默**：板子回 OK、目标程序毫无反应。所以按键前必须先核对。
    """
    h = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(h)
    if n <= 0:
        return "OK fg="
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, buf, n + 1)
    return "OK fg=%s" % buf.value


class H(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(5.0)
        while True:
            try:
                line = self.rfile.readline()
            except Exception:
                return
            if not line:
                return
            cmd = line.decode("utf-8", "replace").strip().lower()
            if cmd in ("q", "cursor", "pos"):
                out = 读()
            elif cmd in ("f", "fg", "front"):
                out = 前台()
            elif cmd in ("bye", "quit"):
                self.wfile.write(b"OK bye\n")
                return
            else:
                out = "ERR 只认 q / f / bye"
            try:
                self.wfile.write((out + "\n").encode())
                self.wfile.flush()
            except Exception:
                return


class S(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8311
    # 只绑回环：A 不直接连它，由 8300 的代理中转，这样不必动防火墙
    with S(("127.0.0.1", port), H) as srv:
        sys.stderr.write("cursor_server 已起 127.0.0.1:%d pid=%d\n" % (port, 0))
        srv.serve_forever()
