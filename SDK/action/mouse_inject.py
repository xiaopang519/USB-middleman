# -*- coding: utf-8 -*-
"""执行层 · 鼠标注入（Interception 驱动级）

把决策层输出的意图"打 (x, y)"翻译成鼠标动作——移动+点击。

为什么用 Interception 不用 SendInput？
  SendInput 是"合成输入"，部分宿主会过滤它（高完整性窗口下甚至静默丢弃，
  返回值还报成功）。Interception 从键鼠驱动层注入，宿主看到的是驱动事件。

工作原理：
  正常：物理鼠标 → 驱动 → 系统 → 宿主
  注入：程序 → Interception驱动 → 系统 → 宿主

⚠ 注意：这是**没有硬件时的软件兜底**。本仓库主线是 RP2040 硬件中介
  （见根目录 README），那才是"宿主看到的就是真键鼠"的做法。

需要管理员权限。

坐标换算：
  Interception move_abs 用 0~65535 的绝对坐标，不是屏幕像素。
  换算：abs_x = 屏幕x * 65535 / (屏幕宽 - 1)

【接口契约】
  输入：屏幕坐标 (x, y) + 动作（移动/点击）
  输出：无（注入硬件事件）
  执行层只听决策层，不自己判断打谁/时机。
"""
import ctypes
import os
import time

user32 = ctypes.windll.user32

# Interception 常量
F_MOVE_ABS = 0x001
L_DOWN, L_UP = 0x001, 0x002
R_DOWN, R_UP = 0x004, 0x008


class MouseStroke(ctypes.Structure):
    """Interception 鼠标事件结构（照 C 头文件）。"""
    _fields_ = [
        ("state", ctypes.c_ushort),
        ("flags", ctypes.c_ushort),
        ("rolling", ctypes.c_short),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("information", ctypes.c_uint),
    ]


class InterceptionMouse:
    """Interception 鼠标控制（移动+点击）。

    DLL 位置：SDK 根目录的 drivers/interception.dll
    需要管理员权限。
    """

    def __init__(self, mouse_dev=11, dll_path=None):
        """加载 DLL + 创建驱动上下文。

        参数:
            mouse_dev: 鼠标设备号（11~20，要试哪个是你的鼠标）
            dll_path: DLL 路径，None 时自动找 drivers/interception.dll
        """
        if dll_path is None:
            # 找 drivers/interception.dll（SDK 根目录下）
            sdk_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            dll_path = os.path.join(sdk_root, "drivers", "interception.dll")
        if not os.path.isfile(dll_path):
            raise FileNotFoundError("找不到 interception.dll: %s" % dll_path)
        self.lib = ctypes.WinDLL(dll_path)
        self.lib.interception_create_context.restype = ctypes.c_void_p
        self.lib.interception_send.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                               ctypes.c_void_p, ctypes.c_uint]
        self.lib.interception_send.restype = ctypes.c_int
        self.ctx = self.lib.interception_create_context()
        if not self.ctx:
            raise RuntimeError("Interception 创建上下文失败——驱动没装 或 不是管理员")
        self.mouse_dev = int(mouse_dev)

    def _send(self, stroke):
        return self.lib.interception_send(self.ctx, self.mouse_dev,
                                          ctypes.byref(stroke), 1) == 1

    def move_abs(self, x, y):
        """移动到屏幕坐标 (x, y)。"""
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        st = MouseStroke()
        st.state = 0
        st.flags = F_MOVE_ABS
        st.rolling = 0
        st.information = 0
        st.x = int(int(x) * 65535 / max(1, screen_w - 1))
        st.y = int(int(y) * 65535 / max(1, screen_h - 1))
        return self._send(st)

    def button(self, name, down):
        """按下/抬起。name: "left"/"right"  down: True=按下 False=抬起"""
        st = MouseStroke()
        st.flags = 0
        st.rolling = 0
        st.information = 0
        st.x = st.y = 0
        if name == "left":
            st.state = L_DOWN if down else L_UP
        elif name == "right":
            st.state = R_DOWN if down else R_UP
        else:
            raise ValueError("按键名必须是 left 或 right")
        return self._send(st)

    def click(self, x, y, name="left", hold_ms=100):
        """完整点击：移动→按下→保持→抬起。"""
        self.move_abs(x, y)
        time.sleep(0.08)
        self.button(name, True)
        time.sleep(max(1, hold_ms) / 1000.0)
        self.button(name, False)

    def destroy(self):
        """释放驱动上下文。"""
        if self.ctx:
            try:
                self.lib.interception_destroy_context(self.ctx)
            except Exception:
                pass
            self.ctx = None
