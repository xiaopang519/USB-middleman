# -*- coding: utf-8 -*-
"""第 2 课的教学小工具 —— 只为让你"看见数字"，不是项目的一部分。

它做的事很少，但每一件都对应一个概念：
    尺寸    → 图 = 宽×高 个点，每个点 3 个数
    看点    → 取某个坐标上的 3 个数（B, G, R）
    数红    → 用一条阈值把"红色像素"挑出来，并随手画一张蒙版图
    数条    → 调项目里真正的 眼\\血条.py，数出几根血条
    存图    → 把某一小块裁出来存成 png，方便你放大看

用法（在项目根目录）：
    python 教学\\第2课.py 尺寸
    python 教学\\第2课.py 看点 960 412
    python 教学\\第2课.py 数红 140
    python 教学\\第2课.py 数条 140
    python 教学\\第2课.py 存图
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "眼"))

候选图 = ["资料/样本/旧_yolo预演_171706.png",
          "证据/现场帧_1310.png", "证据/走一格预演_东_133406.png",
          "证据/现场_1355_世界.png"]
# ★ 默认用「有怪的帧」：血条看得见，数红/数条 的数字才和 PPT 里一致。
#   `数条2` 会切到「只有宠物的帧」，用来对比"同一函数、换张图、结果从 12 变 0"。


def 找图() -> str:
    for rel in 候选图:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if os.path.exists(p):
            return p
    # 兜底：证据目录里第一张 1920x1080 的 png
    d = os.path.join(ROOT, "证据")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith(".png"):
                p = os.path.join(d, fn)
                f = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
                if f is not None and f.shape[:2] == (1080, 1920):
                    return p
    return ""


def 读(path: str):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = 找图()
    if not path:
        print("找不到可用的图。先确认 证据\\ 里有截图。")
        return 1
    图 = 读(path)
    if 图 is None:
        print("读不到：", path)
        return 1
    高, 宽 = 图.shape[:2]
    命令 = sys.argv[1]

    if 命令 == "尺寸":
        print("这张图：%s" % path)
        print("shape = %s   ← 顺序是 (高, 宽, 通道数)" % (图.shape,))
        print("宽 %d 个点 × 高 %d 个点 = %d 个点" % (宽, 高, 宽 * 高))
        print("每个点 3 个数（B、G、R）→ 一共 %d 个数" % (宽 * 高 * 3))
        print("左上角那个点（坐标 0,0）的 3 个数 = %s" % (图[0, 0],))
        print("注意：图[0, 0] 的第一个下标是**行**（y），第二个是**列**（x）")

    elif 命令 == "看点":
        x = int(sys.argv[2]) if len(sys.argv) > 2 else 960
        y = int(sys.argv[3]) if len(sys.argv) > 3 else 412
        if not (0 <= x < 宽 and 0 <= y < 高):
            print("坐标越界：x 要在 0~%d，y 要在 0~%d" % (宽 - 1, 高 - 1))
            return 1
        b, g, r = [int(v) for v in 图[y, x]]
        print("坐标 (x=%d, y=%d) 上的 3 个数：B=%d  G=%d  R=%d" % (x, y, b, g, r))
        print("写成代码就是：图[%d, %d] = [%d, %d, %d]" % (y, x, b, g, r))
        print("★ 注意下标顺序：先 y 后 x。这不是笔误，是 OpenCV 的约定。")

    elif 命令 == "数红":
        阈值 = int(sys.argv[2]) if len(sys.argv) > 2 else 140
        B = 图[:, :, 0].astype(int)
        G = 图[:, :, 1].astype(int)
        R = 图[:, :, 2].astype(int)
        蒙版 = ((R > 阈值) & (G < 110) & (B < 110) & (R > G + 60))
        n = int(蒙版.sum())
        print("把阈值 R>%d 代进项目里的红色判据：" % 阈值)
        print("  (R > %d) & (G < 110) & (B < 110) & (R > G + 60)" % 阈值)
        print("→ 被判为“红色”的像素：%d 个（占全图 %.3f%%）" % (n, n / (宽 * 高) * 100))
        存 = os.path.join(ROOT, "证据", "教学_红蒙版_%d.png" % 阈值)
        蒙版图 = (蒙版.astype(np.uint8) * 255)
        ok, buf = cv2.imencode(".png", 蒙版图)
        buf.tofile(存)
        print("→ 顺便把蒙版存成黑白图：%s" % 存)
        print("  （白=被判为红色，黑=不是。用图片查看器打开看看）")

    elif 命令 in ("数条", "数条2"):
        if 命令 == "数条2":
            # 换成「现场帧」：那一帧只有宠物（品红条），所以红条数会被数成 0
            p2 = os.path.join(ROOT, "证据", "现场帧_1310.png")
            if os.path.exists(p2):
                图 = 读(p2)
                高, 宽 = 图.shape[:2]
                print("换成只有宠物的帧：%s" % p2)
                蒙版 = ((图[:, :, 2].astype(int) > 140) & (图[:, :, 1].astype(int) < 110)
                        & (图[:, :, 0].astype(int) < 110))
                print("（这一帧的红点：%d 个 —— 都是 UI 和衣服上的红，没有血条）" % int(蒙版.sum()))
        import 血条
        bars = 血条.红条(图)
        print("调用项目里真正的函数 眼\\血条.py::红条()")
        print("→ 数出 %d 根血条" % len(bars))
        for i, b in enumerate(bars[:10], 1):
            print("   第%d根：中心x=%d 顶y=%d 宽=%d" % (i, b["中心x"], b["顶y"], b["宽"]))

    elif 命令 == "存图":
        裁 = 图[300:600, 800:1200]          # y 300~600, x 800~1200
        存 = os.path.join(ROOT, "证据", "教学_裁切.png")
        ok, buf = cv2.imencode(".png", 裁)
        buf.tofile(存)
        print("裁了一块：x 800~1200、y 300~600（400×300 个点）")
        print("存成：%s" % 存)
        print("打开它，你看到的每个小点，就是刚才说的“一个点 3 个数”。")

    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
