# -*- coding: utf-8 -*-
"""第 3 课的教学小工具 —— 从"一堆红点"到"一根血条"。

它把项目里 眼\\血条.py 干的事拆成四步，让你看见每一步砍掉了什么：

    ① 颜色      → 一片红点（第 2 课已经会了）
    ② 连通域    → 把挨着的点圈成"块"
    ③ 遮 UI     → 把聊天栏、头像、右侧面板这些不属于"世界"的地方整块清掉
    ④ 看形状    → 宽 25~170、高 2~14、面积 ≥55 才是血条

用法（在项目根目录）：
    python 教学\\第3课.py 漏斗
    python 教学\\第3课.py 存图
    python 教学\\第3课.py 比一比
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

图A = os.path.join(ROOT, "资料", "样本", "旧_yolo预演_171706.png")   # 有怪，但带旧工具的标注框
图B = os.path.join(ROOT, "证据", "现场帧_1310.png")                  # 真实现场，只有宠物

# 与 眼\血条.py 完全一致的常数（改动要同步，否则教学与生产不一致）
MIN_W, MAX_W = 25, 170
MIN_H, MAX_H = 2, 14
MIN_AREA = 55
UI = {"y_from": 790, "corner": (120, 270), "x_from": 1770}


def 读(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def 红掩膜(图):
    b = 图[:, :, 0].astype(int)
    g = 图[:, :, 1].astype(int)
    r = 图[:, :, 2].astype(int)
    return ((r > 140) & (g < 110) & (b < 110) & (r > g + 60)).astype(np.uint8)


def 去UI(m):
    n = m.copy()
    n[UI["y_from"]:, :] = 0
    n[:UI["corner"][0], :UI["corner"][1]] = 0
    n[:, UI["x_from"]:] = 0
    return n


def 块们(m):
    n, lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for k in range(1, n):
        x, y, w, h, area = (int(v) for v in st[k][:5])
        out.append({"x": x, "y": y, "w": w, "h": h, "面积": area})
    return out


def 合形状(块) -> bool:
    return MIN_W <= 块["w"] <= MAX_W and MIN_H <= 块["h"] <= MAX_H and 块["面积"] >= MIN_AREA


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    命令 = sys.argv[1]
    p = 图A if os.path.exists(图A) else 图B
    图 = 读(p)
    if 图 is None:
        print("读不到图：", p)
        return 1
    m0 = 红掩膜(图)
    m1 = 去UI(m0)
    全部 = 块们(m0)
    去ui = 块们(m1)
    合格 = [b for b in 去ui if 合形状(b)]
    不合格 = [b for b in 去ui if not 合形状(b)]

    if 命令 == "漏斗":
        print("图：%s" % p)
        print()
        print("① 红色掩膜            → %d 个红点" % int(m0.sum()))
        print("② 连通域（不遮 UI）   → %d 块" % len(全部))
        print("③ 遮掉 UI 后再连通    → %d 块   （砍掉 %d 块）" % (len(去ui), len(全部) - len(去ui)))
        print("④ 只留形状合格的      → %d 根血条" % len(合格))
        print()
        print("形状规则：宽 %d~%d、高 %d~%d、面积 ≥%d" % (MIN_W, MAX_W, MIN_H, MAX_H, MIN_AREA))
        print()
        print("前 8 块（看尺寸，自己判断为什么被留或被砍）：")
        for i, b in enumerate(去ui[:8], 1):
            print("   第%d块  宽%3d 高%3d 面积%5d   %s"
                  % (i, b["w"], b["h"], b["面积"], "✅ 留" if 合形状(b) else "❌ 砍"))

    elif 命令 == "比一比":
        print("被砍掉的块（前 5 个）—— 看看它们的尺寸怪在哪：")
        for b in 不合格[:5]:
            print("   宽%4d 高%3d 面积%6d   宽高比 %.1f" % (b["w"], b["h"], b["面积"],
                                                    b["w"] / max(1, b["h"])))
        print()
        print("留下的块（前 5 个）—— 血条长什么样：")
        for b in 合格[:5]:
            print("   宽%4d 高%3d 面积%6d   宽高比 %.1f" % (b["w"], b["h"], b["面积"],
                                                    b["w"] / max(1, b["h"])))
        print()
        print("★ 血条是“细长条”：宽是高的几倍。框边、字、衣服上的红，形状都不长这样。")

    elif 命令 == "存图":
        画 = 图.copy()
        for b in 不合格:
            cv2.rectangle(画, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]),
                          (255, 0, 0), 1)          # 蓝 = 被砍掉
        for i, b in enumerate(合格, 1):
            cv2.rectangle(画, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]),
                          (0, 255, 0), 2)          # 绿 = 判为血条
            cv2.putText(画, str(i), (b["x"], max(12, b["y"] - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1, cv2.LINE_AA)
        存 = os.path.join(ROOT, "证据", "教学_连通域.png")
        ok, buf = cv2.imencode(".png", 画)
        buf.tofile(存)
        print("绿框 = 判为血条（共 %d 根）" % len(合格))
        print("蓝框 = 被形状规则砍掉的块（共 %d 块）" % len(不合格))
        print("存成：%s" % 存)
        print("打开它：你会看到蓝框大多在图标、文字、框边上 —— 它们也是红的，但不是血条。")

    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
