# -*- coding: utf-8 -*-
"""example 01 —— 眼：采集还在不在、帧新不新、画面多大。

只读。不碰手，也不动用任何安全开关。

    python examples/01_eye_check.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "眼"))

import eye  # noqa: E402


def main() -> int:
    cfg = eye.load_cfg()
    健 = eye.health(cfg)

    print("帧路径    ", 健["路径"])
    print("帧龄(秒)  ", 健["帧龄秒"], "  上限", 健["最老帧龄秒"])
    print("字节      ", 健["字节"])
    print("形状      ", 健["形状"])

    if not 健["ok"]:
        print("\n眼没通过：", 健["原因"] or "（无说明）")
        print("排查顺序：① ffmpeg 起了吗（PowerShell 里看有没有它）② 设备名对不对 ③ 帧路径存在吗")
        return 1

    # 拿一帧，做一次最朴素的"图色"动作：数某个矩形里有多少像素满足条件。
    frame = eye.grab(cfg)
    if frame is None:
        print("\n能读到文件但解不出一帧（也许是写了一半）—— 重跑一次。")
        return 1

    h, w = frame.shape[:2]
    print("\n解出一帧：%d×%d，中心点 BGR = %s" % (w, h, frame[h // 2, w // 2].tolist()))

    # 演示：统计整帧里"明显偏红"的像素比例（阈值随手给，只作示范）
    b, g, r = frame[:, :, 0], frame[:, :, 1], frame[:, :, 2]
    red = int(((r > 150) & (r > g + 40) & (r > b + 40)).sum())
    print("偏红像素：%d（占 %.3f%%）—— 换成你的目标颜色/形状，就是感知层" % (red, red * 100.0 / (w * h)))
    print("\n结论：眼是活的。下一步跑 examples/02_hand_check.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
