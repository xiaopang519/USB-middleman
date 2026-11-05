# -*- coding: utf-8 -*-
"""gdi.py —— 数当前进程的 GDI 对象，防"跑几小时后整机卡死"。

为什么必须有（2026-10-26 15:3x，从 game-bot-forge 的纪律里拿的逻辑）：
    每次截屏 / 模板匹配 / 建画笔，都会占一个 GDI 对象；漏了就只增不减。
    Windows 每个进程的 GDI 上限默认 **10000**，到顶之后所有绘图 API 全部失败 ——
    表现是"跑了几个小时突然什么都不能画、程序像死了一样"，而且**重启才恢复**。
    自动化脚本最经典的死法就是这个，比逻辑 bug 更常见，因为它在几分钟里完全看不出来。

阈值（照那套纪律）：
    < 3000  正常
    >= 3000 警告（要开始查哪里漏了）
    >= 6000 停手（再往上就危险了）

用法：
    python 工具\\gdi.py              # 数自己
    python 工具\\gdi.py 12345        # 数别的进程
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

GR_GDIOBJECTS = 0
GR_USEROBJECTS = 1

u = ctypes.WinDLL("user32", use_last_error=True)
u.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
u.GetGuiResources.restype = wintypes.DWORD
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.GetCurrentProcess.restype = wintypes.HANDLE

警告 = 3000
停手 = 6000


def _句柄(pid: int | None):
    if not pid or pid == os.getpid():
        return k.GetCurrentProcess(), None
    PROCESS_QUERY_INFORMATION = 0x0400
    h = k.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    return h, h


def 数(pid: int | None = None) -> dict:
    h, 要关 = _句柄(pid)
    if not h:
        return {"ok": False, "原因": "打不开进程 %s" % pid}
    try:
        gdi = int(u.GetGuiResources(h, GR_GDIOBJECTS))
        用户 = int(u.GetGuiResources(h, GR_USEROBJECTS))
    finally:
        if 要关:
            k.CloseHandle(要关)
    级 = "正常" if gdi < 警告 else ("警告" if gdi < 停手 else "停手")
    return {"ok": True, "gdi": gdi, "user": 用户, "级别": 级,
            "警告线": 警告, "停手线": 停手,
            "该停手": gdi >= 停手, "该警告": gdi >= 警告}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
    r = 数(pid)
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") and not r.get("该停手") else 1


if __name__ == "__main__":
    sys.exit(main())
