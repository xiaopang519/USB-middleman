# -*- coding: utf-8 -*-
"""决策层 · 决策引擎

从感知层给的目标列表里，选一个打，并且锁定到死。

核心思想：
  自动化不是"见怪就打"，而是"锁定一只打到死"。
  否则每帧都挑新的 → 技能全打空 → 啥都打不死。

两个核心规则：
  1. 选最近的：算每个目标到画面中心的距离，最近的优先
  2. 锁定击杀：选定后，下一帧优先找"它"——位置近的同一个
     血条消失超时 → 判定已死 → 解锁换下一个
     血条没少超时 → 判定打不动 → 放弃解锁

画面中心 = 角色位置（俯视角，角色永远在画面中心）

【接口契约】
  输入：感知层给的目标列表 [{"cx":int, "bar_y":int, "bar_w":int}, ...]（客户区坐标）
  输出：单个目标 {"cx","bar_y","bar_w"} 或 None
  决策层不依赖感知层怎么实现——换识别方法不影响决策。
"""
import time

# 画面中心 = 画面尺寸的一半（角色在这）
# 换分辨率要改这里，或从配置读
CX, CY = 640, 512

# 锁定超时（秒）
DEAD_TIMEOUT = 1.5       # 目标消失多久判死
STUCK_TIMEOUT = 45.0     # 目标多久没少判打不动


class DecisionEngine:
    """决策引擎：决定打哪个目标，什么时候换。

    状态：
      lock_target: 当前锁定的目标（None=没锁定）
      lock_at: 锁定时间
      last_bar_w: 上次看到的目标宽（=血量）
      last_seen: 最后一次看到目标的时间
    """

    def __init__(self, cx=CX, cy=CY):
        self.cx = cx
        self.cy = cy
        self.lock_target = None
        self.lock_at = 0.0
        self.last_bar_w = 0
        self.last_seen = 0.0
        self.now = 0.0

    def update(self, targets, now=None):
        """每帧调用：传当前识别到的目标列表，返回要打的目标。

        参数:
            targets: [{"cx":int, "bar_y":int, "bar_w":int}, ...]
            now: 当前时间戳，None 时用 time.time()

        返回: 要打的目标 dict 或 None（没目标）
        """
        self.now = now if now is not None else time.time()
        if not targets:
            self._check_timeout()
            return None
        if self.lock_target is not None:
            t = self._relock(targets)
            if t is not None:
                return t
            self._check_timeout()
        return self._pick_nearest(targets)

    def _pick_nearest(self, targets):
        """选离画面中心最近的。"""
        best = None
        best_d = 1e9
        for m in targets:
            d = self._dist(m)
            if d < best_d:
                best_d, best = d, m
        if best is not None:
            self.lock_target = best
            self.lock_at = self.now
            self.last_bar_w = best["bar_w"]
            self.last_seen = self.now
        return best

    def _relock(self, targets):
        """找锁定目标现在还在不在（位置近=同一个）。"""
        if not self.lock_target:
            return None
        cx0 = self.lock_target["cx"]
        cy0 = self.lock_target["bar_y"]
        for m in targets:
            if abs(m["cx"] - cx0) <= 50 and abs(m["bar_y"] - cy0) <= 30:
                self.last_bar_w = m["bar_w"]
                self.last_seen = self.now
                self.lock_target = m
                return m
        return None

    def _dist(self, m):
        """目标到画面中心的距离。"""
        return ((m["cx"] - self.cx) ** 2 + (m["bar_y"] - self.cy) ** 2) ** 0.5

    def _check_timeout(self):
        """检查锁定目标是否应该解锁。"""
        if self.lock_target is None:
            return
        if self.now - self.last_seen > DEAD_TIMEOUT:
            print("  [解锁] 目标消失 %ds → 判定已死" % (self.now - self.last_seen))
            self.lock_target = None
            return
        if self.now - self.lock_at > STUCK_TIMEOUT:
            print("  [解锁] 锁定 %ds 没少 → 判定打不动" % STUCK_TIMEOUT)
            self.lock_target = None
            return

    def reset(self):
        """重置引擎状态（换目标/重启时用）。"""
        self.lock_target = None
        self.lock_at = 0.0
        self.last_bar_w = 0
        self.last_seen = 0.0
