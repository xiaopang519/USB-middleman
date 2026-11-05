# -*- coding: utf-8 -*-
"""系统层 · 热键急停

全局热键，不靠鼠标——抢鼠标时也能停。

热键：
  Ctrl+Alt+Q = 急停（立即停止主循环）
  Ctrl+Alt+P = 暂停/继续切换（暂停时鼠标归还用户）

原理：用 GetAsyncKeyState 轮询键盘状态，不注册系统热键（避免冲突）。
主循环每帧调 check() 检查热键状态。

【接口契约】
  输入：无（轮询）
  输出：(stop: bool, pause_toggle: bool)
        stop=True → 主循环停止
        pause_toggle=True → 切换暂停状态
"""
import ctypes

user32 = ctypes.windll.user32

VK_CONTROL = 0x11
VK_MENU    = 0x12   # Alt
VK_Q       = 0x51   # Q → 急停
VK_P       = 0x50   # P → 暂停/继续
GetAsyncKeyState = user32.GetAsyncKeyState


def check_hotkeys():
    """检查热键。返回 (stop, pause_toggle)。

    主循环每帧调一次（开头 + 点击前各一次更稳）。
    """
    ctrl = GetAsyncKeyState(VK_CONTROL) & 0x8000
    alt  = GetAsyncKeyState(VK_MENU) & 0x8000
    q    = GetAsyncKeyState(VK_Q) & 0x8000
    p    = GetAsyncKeyState(VK_P) & 0x8000
    stop = bool(ctrl and alt and q)
    pause_toggle = bool(ctrl and alt and p)
    return stop, pause_toggle
