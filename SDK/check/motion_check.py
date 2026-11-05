# -*- coding: utf-8 -*-
"""校验层 · 帧差校验

对比前后帧，判断角色动没动。不动=卡死 → 触发突围。

原理：
  抓当前帧和上一帧，算两帧的像素差异。
  差异大 = 画面变了 = 角色动了
  差异小 = 画面没变 = 角色卡住了

注意：界面特效/目标闪烁会带来小差异，阈值要调到"只有画面真实变化才算动"。

【接口契约】
  输入：当前帧 PIL Image
  输出：bool（True=动了, False=没动可能卡死）
"""
import numpy as np


class MotionChecker:
    """帧差校验器。"""

    def __init__(self, threshold=2000):
        """
        参数:
            threshold: 帧差"变化像素数"阈值，小于这个算"没动"
                       默认 2000，界面特效多要调大
        """
        self.last = None
        self.threshold = threshold

    def moved(self, frame):
        """返回 True=动了, False=没动(可能卡死)"""
        arr = np.array(frame)
        if self.last is None:
            self.last = arr
            return True  # 第一帧默认算"动了"
        # 两帧每个像素的差，差异>30 的算"变了"
        diff = np.abs(arr.astype(int) - self.last.astype(int))
        changed = (diff.sum(axis=2) > 30).sum()
        self.last = arr
        return changed > self.threshold

    def reset(self):
        """重置（换窗口/重启时用）。"""
        self.last = None
