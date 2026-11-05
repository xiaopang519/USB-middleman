# -*- coding: utf-8 -*-
"""感知层 · 抓帧

用句柄(hwnd)截取窗口客户区一帧画面，返回 PIL 图像。

为什么要截"客户区"不是"整个窗口"？
  窗口区 = 标题栏 + 边框 + 客户区
  客户区 = 纯画面区域
  截整个窗口会带标题栏/边框，画面位置偏移，后面识别全错。

原理：
  1. ClientToScreen：客户区左上角(0,0) → 屏幕坐标
  2. GetClientRect：拿客户区宽高
  3. ImageGrab.grab(bbox)：截那块区域
  bbox = (屏幕左, 屏幕上, 屏幕左+宽, 屏幕上+高)  注意是右下不是宽高
"""
import ctypes
import ctypes.wintypes as wt
from PIL import ImageGrab

from perception.find_window import enum_windows

user32 = ctypes.windll.user32


def get_client_rect(hwnd):
    """拿窗口客户区在屏幕上的位置和大小。

    返回: (左, 上, 宽, 高) —— 屏幕坐标
    """
    point = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    return point.x, point.y, rect.right, rect.bottom


def get_client_origin(hwnd):
    """只拿客户区左上角的屏幕坐标（用于坐标换算）。"""
    point = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y


def grab_frame(hwnd):
    """截取窗口客户区一帧画面，返回 PIL 图像。

    参数:
        hwnd: 窗口句柄

    返回: PIL.Image 或 None（窗口无效时）
    """
    try:
        x, y, w, h = get_client_rect(hwnd)
        if w <= 0 or h <= 0:
            return None
        return ImageGrab.grab(bbox=(x, y, x + w, y + h))
    except Exception:
        return None


if __name__ == "__main__":
    wins = enum_windows()
    if not wins:
        print("没找到目标窗口")
        exit()
    w = wins[0]
    print("找到窗口: %s  句柄=%d" % (w["title"], w["hwnd"]))
    img = grab_frame(w["hwnd"])
    if img:
        print("抓到画面: %dx%d" % (img.width, img.height))
        img.save("frame.png")
        print("已保存 frame.png")
    else:
        print("抓帧失败")
