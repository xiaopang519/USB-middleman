# -*- coding: utf-8 -*-
r"""屏幕流.py —— 持续采流，永远保留"最后一帧"，谁要看都是同一帧。

为什么（2026-10-26 22:3x，作者 指出）：
    "你是不是只抓一帧？抓帧是实时的，必须要抓屏幕流，最后一帧为参考，
     手才会有逻辑，不然就是打一下就不打了，因为不知道打哪里。"

    实测：采集卡 → ffmpeg 一直在写 `C:\Users\you\dsh_capture\latest.png`，帧龄 0.008 秒，
    **画面本身是活的**。但 `eye.grab()` 每叫一次就重新读 2.8MB 文件 + 解 PNG = 36ms。
    于是：
        · 连续动作里"先读数值"、"再悬停"、"再按键"、"再确认"——每一步都各自重新抓，
          抓到的其实是不同时刻的画面，前后对不上 → 做到一半"不知道该动哪里了"
        · 4 个模块各抓各的，同一次决策用的不是同一帧
        · 36ms × 每步若干次，整个循环被解码时间拖慢

    这里改成：**一个线程持续盯着文件，一变就解码，永远保留最新那一帧（带时间戳）**。
    所有消费者 `最新()` 都是零成本拿到**同一帧**，并且知道它多旧。

用法：
    import 屏幕流
    屏幕流.启动(cfg)                 # 幂等，重复调没事
    f, 龄 = 屏幕流.最新()             # 立刻拿到最后一帧（龄 0 表示刚更新）
    屏幕流.统计()                    # 采了多少帧、多少 fps、当前帧龄
"""
from __future__ import annotations

import os
import sys
import threading
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for 子 in ("眼", "手", "脑"):
    sys.path.insert(0, os.path.join(ROOT, 子))

import eye  # noqa: E402

锁 = threading.Lock()
帧 = None            # 最新解码好的画面
帧时 = 0.0           # 它是什么时候被采到的（time.time）
源签名 = None        # (mtime_ns, size) 用来判断文件变没变
线程 = None
在跑 = False
采帧数 = 0
启动时刻 = 0.0
上一帧时 = 0.0
间隔秒 = 0.0          # 两次采到新帧之间的时间（真实 fps 的倒数）


def _源路径(cfg) -> str | None:
    spec = (cfg or eye.load_cfg()).get("眼") or {}
    return spec.get("帧路径")


def _循环(cfg, 最短间隔=0.004):
    global 帧, 帧时, 源签名, 在跑, 采帧数, 上一帧时, 间隔秒
    src = _源路径(cfg)
    上次改动 = None
    while 在跑:
        try:
            if not src or not os.path.exists(src):
                time.sleep(0.05)
                continue
            st = os.stat(src)
            签名 = (st.st_mtime_ns, st.st_size)
            if 签名 == 上次改动:
                time.sleep(最短间隔)          # 没变就等，不浪费 CPU
                continue
            上次改动 = 签名
            data = None
            for _k in range(4):                # 写的人刚好在写 → 短退避
                try:
                    with open(src, "rb") as fh:
                        data = fh.read()
                    break
                except (PermissionError, OSError):
                    time.sleep(0.006 * (_k + 1))
            if not data:
                continue
            img = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            现在 = time.time()
            with 锁:
                帧 = img
                帧时 = 现在
                源签名 = 签名
                采帧数 += 1
                if 上一帧时:
                    间隔秒 = 现在 - 上一帧时
                上一帧时 = 现在
        except Exception:
            time.sleep(0.05)
        time.sleep(最短间隔)


def 启动(cfg=None) -> bool:
    """开始采流（幂等）。返回是否已经在跑。"""
    global 线程, 在跑, 启动时刻
    if 在跑 and 线程 is not None and 线程.is_alive():
        return True
    cfg = cfg or eye.load_cfg()
    if not _源路径(cfg):
        return False
    在跑 = True
    启动时刻 = time.time()
    线程 = threading.Thread(target=_循环, args=(cfg,), daemon=True, name="屏幕流")
    线程.start()
    return True


def 停止():
    global 在跑
    在跑 = False


def 最新(最老秒: float = 1.5):
    """拿到最后一帧。返回 (frame, 帧龄秒)；没有帧 → (None, None)。"""
    with 锁:
        f, t = 帧, 帧时
    if f is None:
        return None, None
    龄 = max(0.0, time.time() - t)
    if 龄 > 最老秒:
        return None, 龄           # 太旧就当没看见（不猜）
    return f, 龄


def 统计() -> dict:
    with 锁:
        n, t, iv = 采帧数, 帧时, 间隔秒
    return {"在跑": bool(在跑 and 线程 and 线程.is_alive()),
            "采帧数": n,
            "fps": round(1.0 / iv, 1) if iv else None,
            "帧龄": round(max(0.0, time.time() - t), 3) if t else None,
            "跑了几秒": round(time.time() - 启动时刻, 1) if 启动时刻 else 0}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json
    cfg = eye.load_cfg()
    秒 = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    if not 启动(cfg):
        print("启动失败：配置里没有 眼.帧路径")
        return 2
    t0 = time.time()
    取了 = 0
    while time.time() - t0 < 秒:
        f, 龄 = 最新()
        if f is not None:
            取了 += 1
        time.sleep(0.1)
    print(json.dumps({"统计": 统计(), "%.0f秒内取到帧次数" % 秒: 取了}, ensure_ascii=False))
    停止()
    return 0


if __name__ == "__main__":
    sys.exit(main())
