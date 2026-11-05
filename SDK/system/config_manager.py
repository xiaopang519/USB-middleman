# -*- coding: utf-8 -*-
"""系统层 · 配置管理

管系统配置：读什么、怎么改、改完怎么生效。

配置项：
  target_keyword str   ""           找窗口关键字（换目标程序时改这里）
  loop_interval float 0.3          主循环间隔(秒)
  mouse_dev     int   11           Interception鼠标设备号(重启生效)
  hotkey_stop   str   "ctrl+alt+q" 急停热键(重启生效)
  hotkey_pause  str   "ctrl+alt+p" 暂停热键(重启生效)
  lock_timeout  float 45.0         锁定超时(秒)

约束：
  1. 配置文件是JSON
  2. 没有配置文件时用默认值，自动生成
  3. 立即生效的：每帧从config读
  4. 重启生效的：改完提示"需重启"
"""
import json
import os

DEFAULTS = {
    "target_keyword": "",         # 换目标程序时改这里
    "loop_interval": 0.3,
    "mouse_dev": 11,
    "hotkey_stop": "ctrl+alt+q",
    "hotkey_pause": "ctrl+alt+p",
    "lock_timeout": 45.0,
}

RESTART_REQUIRED = {"mouse_dev", "hotkey_stop", "hotkey_pause"}


class ConfigManager:
    """配置管理。"""

    def __init__(self, path="config.json"):
        self.path = path
        self.config = {}

    def load(self):
        """启动时读配置文件。没有 → 用DEFAULTS + 自动生成。"""
        if not os.path.isfile(self.path):
            self.config = dict(DEFAULTS)
            self.save(self.config)
            print("[配置] 没找到 %s，已用默认值生成" % self.path)
            return self.config
        with open(self.path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.config = dict(DEFAULTS)
        self.config.update(loaded)
        print("[配置] 已加载 %s" % self.path)
        return self.config

    def save(self, config_dict):
        """写回JSON。"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(config_dict, f, ensure_ascii=False, indent=2)
        self.config = dict(config_dict)

    def get(self, key):
        """读单个配置项。"""
        return self.config.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        """改单个配置项。返回 bool：True=需重启生效。"""
        self.config[key] = value
        self.save(self.config)
        return key in RESTART_REQUIRED
