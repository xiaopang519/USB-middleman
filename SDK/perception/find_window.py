# -*- coding: utf-8 -*-
"""感知层 · 找窗口

从所有窗口里找到目标游戏窗口，拿到句柄(hwnd)。

句柄是后续所有操作（截图、点击）的锚——没有句柄什么都做不了。

原理：EnumWindows 遍历所有窗口 → 看可见 → 读标题 → 标题含关键字 = 目标窗口。
"""
import ctypes
import ctypes.wintypes as wt

# === 配置：要找的窗口标题关键字 ===
# 换游戏时改这里（或从 config.json 读）
GAME_KEYWORD = ""  # 例: "你的游戏名" 或窗口标题里的特征词

user32 = ctypes.windll.user32

# EnumWindows 回调类型：WINFUNCTYPE(返回bool, HWND, LPARAM)
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def enum_windows(keyword=None):
    """枚举所有可见窗口，返回标题含 keyword 的窗口列表。

    参数:
        keyword: 标题关键字，None 时用 GAME_KEYWORD

    返回: [{"hwnd": 句柄, "title": 标题, "rect": [左,上,宽,高]}, ...]
          坐标系：rect 是屏幕坐标
    """
    kw = keyword or GAME_KEYWORD
    if not kw:
        raise ValueError("未设置 GAME_KEYWORD，请配置游戏窗口标题关键字")
    found = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if kw in title:
            rect = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append({
                "hwnd": hwnd,
                "title": title,
                "rect": [rect.left, rect.top,
                         rect.right - rect.left, rect.bottom - rect.top]
            })
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


if __name__ == "__main__":
    kw = GAME_KEYWORD or input("输入游戏窗口标题关键字: ")
    print("正在查找标题含 %r 的窗口..." % kw)
    wins = enum_windows(kw)
    if not wins:
        print("没找到！请确认游戏已打开、窗口标题含 %r" % kw)
    else:
        print("找到 %d 个窗口：" % len(wins))
        for w in wins:
            print("  标题: %s" % w["title"])
            print("  句柄: %d" % w["hwnd"])
            print("  位置: 左=%d 上=%d 宽=%d 高=%d" % tuple(w["rect"]))
