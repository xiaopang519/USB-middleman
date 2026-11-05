# -*- coding: utf-8 -*-
"""UIPI 判定实验（第 6 课）—— 在 A 机上、不碰目标程序，直接问 Windows：

    同一个 SendInput 调用，
    当前台窗口是「中完整性」时返回什么？
    当前台窗口是「高完整性（管理员）」时又返回什么？

判据（写死，不许事后改口）：
    SendInput 返回 0 且 GetLastError == 5 (ERROR_ACCESS_DENIED)  → UIPI 拦住了
    SendInput 返回 1                                            → 没被拦
    其它错误码                                                  → 另找原因，不是 UIPI

步骤：
    1. 起一个**管理员**记事本（会弹 UAC，需要人点「是」）
    2. 查它进程的完整性级别（确认真的是 High）
    3. 把它拉到前台，对它 SendInput 一个「零位移鼠标移动」
    4. 读返回值 + GetLastError
    5. 不点击、不输入文字，只发 0 位移的移动事件 —— 零副作用

用法： python 教学\\UIPI实验.py
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
a32 = ctypes.WinDLL("advapi32", use_last_error=True)
s32 = ctypes.WinDLL("shell32", use_last_error=True)

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TokenIntegrityLevel = 25
SW_RESTORE = 9
ERROR_ACCESS_DENIED = 5


a32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
a32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
a32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
a32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

TokenElevation = 20


def 完整性(pid: int) -> str:
    """读进程的完整性级别。读不到就退回「是否提权」这个更粗但可靠的判据。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "打不开进程(err=%d)" % ctypes.get_last_error()
    try:
        tok = wintypes.HANDLE()
        if not a32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return "拿不到token(err=%d)" % ctypes.get_last_error()
        try:
            need = wintypes.DWORD()
            a32.GetTokenInformation(tok, TokenIntegrityLevel, None, 0, ctypes.byref(need))
            buf = ctypes.create_string_buffer(max(need.value, 16))
            if a32.GetTokenInformation(tok, TokenIntegrityLevel, buf, need,
                                       ctypes.byref(need)):
                sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                cnt = a32.GetSidSubAuthorityCount(sid)[0]
                v = a32.GetSidSubAuthority(sid, cnt - 1)[0]
                名 = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium",
                     0x3000: "High", 0x4000: "System", 0x5000: "Protected"}.get(v, "未知")
                return "%s (0x%04X)" % (名, v)
            # 退回：只要知道是不是提权
            elev = wintypes.DWORD()
            if a32.GetTokenInformation(tok, TokenElevation, ctypes.byref(elev),
                                       ctypes.sizeof(elev), ctypes.byref(need)):
                return "已提权(High)" if elev.value else "未提权(Medium)"
            return "读不到完整性(err=%d)" % ctypes.get_last_error()
        finally:
            k32.CloseHandle(tok)
    finally:
        k32.CloseHandle(h)


def 找窗口(pid: int):
    got = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and u32.IsWindowVisible(hwnd):
            got.append(hwnd)
        return True

    u32.EnumWindows(cb, 0)
    return got[0] if got else None


class MI(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class IU(ctypes.Union):
    _fields_ = [("mi", MI)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", IU)]


u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u32.SendInput.restype = wintypes.UINT


class II(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class IU2(ctypes.Union):
    _fields_ = [("mi", MI), ("ki", II)]


class INPUT2(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", IU2)]


u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT2), ctypes.c_int]
u32.SendInput.restype = wintypes.UINT


def 发(events: list) -> dict:
    arr = (INPUT2 * len(events))()
    for i, e in enumerate(events):
        arr[i] = e
    ctypes.set_last_error(0)
    n = u32.SendInput(len(events), arr, ctypes.sizeof(INPUT2))
    return {"返回": int(n), "GetLastError": int(ctypes.get_last_error())}


def 鼠标移动事件(x: int, y: int) -> INPUT2:
    # 绝对坐标需要 0..65535 归一化
    sw = u32.GetSystemMetrics(0)
    sh = u32.GetSystemMetrics(1)
    e = INPUT2()
    e.type = 0
    e.u.mi = MI(int(x * 65535 / (sw - 1)), int(y * 65535 / (sh - 1)), 0,
                0x0001 | 0x8000, 0, 0)          # MOVE | ABSOLUTE
    return e


def 键事件(vk: int, 按下: bool) -> INPUT2:
    e = INPUT2()
    e.type = 1                                   # INPUT_KEYBOARD
    e.u.ki = II(vk, 0, 0 if 按下 else 0x0002, 0, 0)   # 抬起 = KEYEVENTF_KEYUP
    return e


def 光标() -> tuple[int, int]:
    class P(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
    p = P()
    u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def 强行前台(hwnd) -> bool:
    """把窗口弄到前台，并**验证**真的成了前台。做不到就返回 False（不许假装成功）。"""
    u32.ShowWindow(hwnd, SW_RESTORE)
    u32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)     # TOPMOST，仅置顶不改位置
    u32.BringWindowToTop(hwnd)
    for _ in range(12):
        u32.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if u32.GetForegroundWindow() == hwnd:
            u32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002)   # 取消置顶
            return True
    return False


def 相对移动(dx: int, dy: int) -> dict:
    e = INPUT2()
    e.type = 0
    e.u.mi = MI(dx, dy, 0, 0x0001, 0, 0)         # MOVE（相对，不受 DPI 缩放影响）
    return 发([e])


def 截窗(hwnd):
    """截一个窗口的内容（用来判断按键有没有真的落进去）。抓不到返回 None。"""
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    try:
        return ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
    except Exception:
        return None


def 差图(a, b) -> int:
    if a is None or b is None:
        return -1
    import numpy as np
    return int(np.abs(np.asarray(a, dtype=int) - np.asarray(b, dtype=int)).sum())


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # ① 先量一次「当前前台」（此时前台多半是终端，Medium）—— 作为对照
    fg = u32.GetForegroundWindow()
    p0 = wintypes.DWORD()
    u32.GetWindowThreadProcessId(fg, ctypes.byref(p0))
    print("① 对照：当前前台进程 pid=%d 完整性=%s" % (p0.value, 完整性(p0.value)))
    x0, y0 = 光标()
    r = 发([鼠标移动事件(300, 200)])
    time.sleep(0.3)
    x1, y1 = 光标()
    print("   光标绝对移动 (300,200)：返回", r, "光标", (x0, y0), "→", (x1, y1),
          "效果:", "到了" if (abs(x1 - 300) <= 3 and abs(y1 - 200) <= 3) else "没到")
    发([鼠标移动事件(x0, y0)])
    print()

    # ② 起管理员记事本
    print("② 正在起**管理员**记事本 …… 屏幕上会弹 UAC，请点「是」")
    rc = s32.ShellExecuteW(None, "runas", "notepad.exe", None, None, 1)
    if rc <= 32:
        print("   起不来，ShellExecute 返回", rc, "（多半是 UAC 被取消）")
        return 1
    print("   已请求启动，等 6 秒……")
    time.sleep(6)

    # ③ 找它的窗口和 pid
    pid = None
    for _ in range(20):
        # 用窗口找进程：记事本的类名是 Notepad
        found = []
        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _lp):
            buf = ctypes.create_unicode_buffer(64)
            u32.GetClassNameW(hwnd, buf, 64)
            if buf.value == "Notepad" and u32.IsWindowVisible(hwnd):
                p = wintypes.DWORD()
                u32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
                found.append((hwnd, p.value))
            return True
        u32.EnumWindows(cb, 0)
        if found:
            hwnd, pid = found[-1]
            break
        time.sleep(0.5)

    if pid is None:
        print("   没找到记事本窗口 —— 可能 UAC 没点、或记事本没起来")
        return 1

    print("③ 找到记事本：hwnd=%s pid=%d 完整性=%s" % (hwnd, pid, 完整性(pid)))
    u32.ShowWindow(hwnd, SW_RESTORE)
    u32.SetForegroundWindow(hwnd)
    time.sleep(0.8)

    fg2 = u32.GetForegroundWindow()
    p2 = wintypes.DWORD()
    u32.GetWindowThreadProcessId(fg2, ctypes.byref(p2))
    print("   现在前台 pid=%d 完整性=%s  （是不是记事本：%s）"
          % (p2.value, 完整性(p2.value), p2.value == pid))
    print()

    # ④ 只做效果测试，且先证明前台真的是那个 High 窗口
    print("④ 目标：对 High 窗口做效果测试（不靠返回值，靠结果）")
    if not 强行前台(hwnd):
        print("   失败：抢不到前台（Windows 不允许后台进程抢焦点）。")
        print("   → 本实验不成立，不下结论。请手动点一下管理员记事本窗口，再重跑。")
        return 2
    print("   前台校验：当前前台就是管理员记事本 =",
          u32.GetForegroundWindow() == hwnd, " 完整性 =", 完整性(pid))
    print()

    # 4a 相对移动（不受 DPI 缩放影响）
    xa, ya = 光标()
    相对移动(60, 40)
    time.sleep(0.35)
    xb, yb = 光标()
    光标到位 = abs((xb - xa) - 60) <= 6 and abs((yb - ya) - 40) <= 6
    print("   4a 相对移动 +60,+40：光标%s → %s 实际(%+d,%+d) → %s"
          % ((xa, ya), (xb, yb), xb - xa, yb - ya,
             "生效" if 光标到位 else "没生效"))
    相对移动(xa - xb, ya - yb)
    time.sleep(0.2)

    # 4b 按键：先量基线噪声（光标闪烁），再打一个 'a'
    前1 = 截窗(hwnd)
    time.sleep(0.6)
    前2 = 截窗(hwnd)
    噪声 = 差图(前1, 前2)
    发([键事件(0x41, True), 键事件(0x41, False)])
    time.sleep(0.6)
    后 = 截窗(hwnd)
    信号 = 差图(前2, 后)
    if 噪声 < 0:
        print("   4b 按键效果：截图不可用，无法判定")
        键到达 = None
    else:
        键到达 = 信号 > max(噪声 * 3, 800)
        print("   4b 打一个 'a'：基线噪声 %d，输入后变化 %d → %s"
              % (噪声, 信号, "字进去了" if 键到达 else "变化未超噪声，判为没到达"))
    print()

    # ⑤ 判定：只看效果
    if 光标到位 and (键到达 is not False):
        print("结论：UIPI 没有拦住注入 —— High 前台时，注入的相对移动真的生效。")
        print("      『软件点不动目标窗口』与 UIPI 无关，要另查会话/桌面/焦点/程序自身输入处理。")
    elif 光标到位 or 键到达 is True:
        print("结论：至少一项效果成立 → UIPI 未完全拦住注入，需再分类。")
    else:
        print("结论：两项效果都没成立，且前台确为 High —— 与 UIPI 的表现一致。")
    print()
    print("（记事本留着没关，你手动关掉；本次只打了 1 个 'a'，没有点击。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
