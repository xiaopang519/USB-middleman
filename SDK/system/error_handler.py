# -*- coding: utf-8 -*-
"""系统层 · 异常恢复

让"一个错不崩全局"。

异常分级：
  致命 FATAL       停止主循环 + 报警
  可恢复 RECOVERABLE 跳过这帧
  已知 KNOWN       静默

连续N次同一种可恢复异常 → 升级致命。

【接口契约】
  输入：异常对象 + 层名（"感知"/"决策"/"执行"/"校验"）
  输出："skip"（跳过这帧）或 "stop"（停止主循环）
"""
FATAL = "fatal"
RECOVERABLE = "recoverable"
KNOWN = "known"

ESCALATE_THRESHOLD = 10  # 连续多少次可恢复 → 升级致命

FATAL_EXCEPTIONS = (
    OSError,
    KeyError,
)

FATAL_KEYWORDS = ["window", "hwnd", "配置", "驱动"]


class ErrorHandler:
    """异常恢复。"""

    def __init__(self):
        self.fail_counts = {}
        self.logs = []

    def _classify(self, e):
        """判异常级别。"""
        if isinstance(e, FATAL_EXCEPTIONS):
            return FATAL
        msg = str(e).lower()
        for kw in FATAL_KEYWORDS:
            if kw.lower() in msg:
                return FATAL
        return RECOVERABLE

    def handle(self, e, layer):
        """主循环调：传入异常+层名，返回跳过还是停止。

        参数:
          e     Exception
          layer str  "感知"/"决策"/"执行"/"校验"

        返回: "skip" 或 "stop"
        """
        level = self._classify(e)
        if level == FATAL:
            self.log_error(FATAL, "[%s] %s: %s" % (layer, type(e).__name__, e))
            return "stop"
        self.fail_counts[layer] = self.fail_counts.get(layer, 0) + 1
        if self.fail_counts[layer] >= ESCALATE_THRESHOLD:
            self.log_error(FATAL, "[%s] 连续 %d 次失败，升级致命"
                           % (layer, self.fail_counts[layer]))
            return "stop"
        self.log_error(RECOVERABLE, "[%s] %s: %s（第%d次，跳过）"
                       % (layer, type(e).__name__, e, self.fail_counts[layer]))
        return "skip"

    def log_error(self, level, msg):
        """记错误日志。"""
        prefix = {"fatal": "[致命]", "recoverable": "[可恢复]",
                  "known": "[已知]"}.get(level, "[?]")
        print("%s %s" % (prefix, msg))
        self.logs.append((level, msg))
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

    def reset(self, layer):
        """某层这帧成功了，清计数。"""
        self.fail_counts[layer] = 0
