r"""录制B机.py —— 把采集卡那一路（B 机画面）录成视频。

为什么不直接从采集卡再开一路：
    `USB3 Video` 这个设备已经被管道里那个 ffmpeg（PID 常驻）占着，
    再开第二个 dshow 抓同一台设备会撞、两个都不稳。
    所以这里**读现成的 latest.png 帧**（采集卡→ffmpeg→latest.png，10fps 一直在更新），
    按固定帧率取帧 → 用管道喂给一个新的 ffmpeg 编码成 mp4。

用法：
    python 工具\录制B机.py                 # 录 20 分钟，5fps
    python 工具\录制B机.py --秒 60 --fps 10
输出：证据\录制\B机画面_<时间>.mp4
进度：每 30 秒打一行（已写帧数、文件大小、实际拿到帧的比例）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for 子 in ("眼", "手", "脑"):
    sys.path.insert(0, os.path.join(ROOT, 子))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

FFMPEG = r"C:\Users\you\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"
录制目录 = os.path.join(ROOT, "证据", "录制")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    a = sys.argv[1:]
    秒 = float(a[a.index("--秒") + 1]) if "--秒" in a else 1200.0
    fps = float(a[a.index("--fps") + 1]) if "--fps" in a else 5.0

    import eye
    cfg = eye.load_cfg()
    帧路径 = (cfg.get("眼") or {}).get("帧路径")
    if not 帧路径 or not os.path.exists(帧路径):
        print("找不到帧源：%s" % 帧路径)
        return 2
    # 先取一帧，拿到真实分辨率
    数据 = None
    for _ in range(20):
        try:
            with open(帧路径, "rb") as fh:
                数据 = fh.read()
            if 数据:
                break
        except OSError:
            pass
        time.sleep(0.05)
    if not 数据:
        print("读不到帧")
        return 2
    首帧 = cv2.imdecode(np.frombuffer(数据, np.uint8), cv2.IMREAD_COLOR)
    高, 宽 = 首帧.shape[:2]
    print("B 机画面 %dx%d，录 %.0f 秒，%g fps（共 %d 帧）"
          % (宽, 高, 秒, fps, int(秒 * fps)))

    os.makedirs(录制目录, exist_ok=True)
    输出 = os.path.join(录制目录, "B机画面_%s.mp4" % time.strftime("%Y%m%d_%H%M%S"))
    命令 = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "%dx%d" % (宽, 高),
            "-r", str(fps), "-i", "-",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", 输出]
    子 = subprocess.Popen(命令, stdin=subprocess.PIPE)
    print("输出：%s" % 输出)

    起 = time.time()
    间隔 = 1.0 / fps
    写过 = 0
    新帧 = 0
    上次 = None
    下次报 = 起 + 30
    try:
        while time.time() - 起 < 秒:
            到点 = 起 + 写过 * 间隔
            现在 = time.time()
            if 现在 < 到点:                       # 按固定节奏取帧，保证视频时长对
                time.sleep(min(0.02, 到点 - 现在))
                continue
            try:
                with open(帧路径, "rb") as fh:
                    数据 = fh.read()
            except OSError:
                数据 = None
            帧 = None
            if 数据:
                帧 = cv2.imdecode(np.frombuffer(数据, np.uint8), cv2.IMREAD_COLOR)
            if 帧 is None:
                帧 = 首帧                            # 读不到就重复上一帧，不中断录制
            elif 上次 is not None and 帧 is not 上次:
                新帧 += 1
            上次 = 帧
            if 帧.shape[0] != 高 or 帧.shape[1] != 宽:
                帧 = cv2.resize(帧, (宽, 高))
            try:
                子.stdin.write(帧.tobytes())
            except (BrokenPipeError, OSError):
                print("编码进程断了")
                break
            写过 += 1
            首帧 = 帧
            if time.time() >= 下次报:
                大小 = os.path.getsize(输出) / 1024 / 1024 if os.path.exists(输出) else 0
                print("  %3.0f 秒  写 %d 帧  新帧 %d  文件 %.1f MB"
                      % (time.time() - 起, 写过, 新帧, 大小))
                下次报 += 30
    finally:
        try:
            子.stdin.close()
        except Exception:
            pass
        子.wait(timeout=30)
    大小 = os.path.getsize(输出) / 1024 / 1024 if os.path.exists(输出) else 0
    print("录完：%d 帧，%d 帧是真的新画面，文件 %.1f MB" % (写过, 新帧, 大小))
    print("路径：%s" % 输出)
    return 0


if __name__ == "__main__":
    sys.exit(main())
