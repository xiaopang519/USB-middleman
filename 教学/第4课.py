# -*- coding: utf-8 -*-
"""第 4 课的教学小工具 —— 机器人怎么认出 "63:119" 这串数字。

核心只有一句话：
    把屏幕上的一个小字剪下来（黑白的），跟字库里的 10 个样本**逐一比分数**，谁高就是谁。

比分数不够（低于门槛）就**承认认不出** —— 这就是项目里"读不到就返回 None，绝不猜"。

用法：
    python 教学\第4课.py 字形墙
    python 教学\第4课.py 切字
    python 教学\第4课.py 比分数
    python 教学\第4课.py 读坐标
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "眼"))

import coords                                    # noqa: E402

图 = os.path.join(ROOT, "证据", "现场帧_1310.png")
框 = coords.DEFAULT_BOX


def 读(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def 取数据():
    f = 读(图)
    x0, y0, x1, y1 = 框
    sub = f[y0:y1, x0:x1]
    return f, sub, coords._group(coords._comps(sub))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    命令 = sys.argv[1]
    # 字库要显式载入（项目里是懒加载）
    lib = coords.load_lib()

    if 命令 == "字形墙":
        片 = []
        for d in "0123456789":
            p = os.path.join(ROOT, "眼", "字形", "%s.png" % d)
            g = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if g is None:
                continue
            g = cv2.resize(g, (g.shape[1] * 6, g.shape[0] * 6), interpolation=cv2.INTER_NEAREST)
            g = cv2.copyMakeBorder(g, 26, 6, 6, 6, cv2.BORDER_CONSTANT, value=0)
            cv2.putText(g, d, (4, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 255, 1, cv2.LINE_AA)
            片.append(g)
        墙 = np.hstack(片)
        存 = os.path.join(ROOT, "证据", "教学_字形墙.png")
        ok, buf = cv2.imencode(".png", 墙)
        buf.tofile(存)
        print("字库：10 个样本，尺寸各不同：")
        for d in "0123456789":
            p = os.path.join(ROOT, "眼", "字形", "%s.png" % d)
            g = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            print("   数字 %s → %d x %d 像素" % (d, g.shape[1], g.shape[0]))
        print()
        print("放大 6 倍拼成一张：%s" % 存)
        print("打开它，看清 3 和 8、6 和 5 差在哪 —— 这就是机器人全部的'常识'。")

    elif 命令 == "切字":
        _f, sub, gs = 取数据()
        print("坐标区：x %d~%d，y %d~%d（宽 %d，高 %d）"
              % (框[0], 框[2], 框[1], 框[3], 框[2] - 框[0], 框[3] - 框[1]))
        print("切出来的组数：%d（项目只取**最后两组**，因为前面可能是别的数字）" % len(gs))
        for i, gp in enumerate(gs, 1):
            文本 = "".join(c["ch"] if "ch" in c else "?" for c in gp)
            print("   第%d组：%d 个字符，尺寸 %s"
                  % (i, len(gp), [(c["m"].shape[1], c["m"].shape[0]) for c in gp]))
        画 = sub.copy()
        for gp in gs:
            for c in gp:
                x, y, w, h = c.get("x"), c.get("y"), c.get("w"), c.get("h")
                if None in (x, y, w, h):
                    continue
                cv2.rectangle(画, (x, y), (x + w, y + h), (0, 255, 0), 1)
        放大 = cv2.resize(画, (画.shape[1] * 3, 画.shape[0] * 3), interpolation=cv2.INTER_NEAREST)
        存 = os.path.join(ROOT, "证据", "教学_切字.png")
        ok, buf = cv2.imencode(".png", 放大)
        buf.tofile(存)
        print()
        print("放大 3 倍、每个字符画绿框：%s" % 存)

    elif 命令 == "比分数":
        _f, sub, gs = 取数据()
        if not gs:
            print("没切出字，换一张图试试")
            return 1
        gp = gs[-1]
        c = gp[0]
        print("取最后一个组的第 1 个字符，尺寸 %d x %d" % (c["m"].shape[1], c["m"].shape[0]))
        分 = sorted(((coords._score(c["m"], p), ch) for ch, p in lib.items()), reverse=True)
        print()
        print("它和 10 个字形逐一比分数（越高越像）：")
        for v, ch in 分:
            print("   数字 %s → %.3f  %s" % (ch, v, "★ 最高" if (v, ch) == 分[0] else ""))
        print()
        print("最高分 %.3f，第二个 %.3f —— 差距就是'像不像'。" % (分[0][0], 分[1][0]))
        print("门槛是 %.2f：低于它的，系统会直接说不认识，而不是猜一个。" % 0.80)

    elif 命令 == "读坐标":
        f = 读(图)
        xy, detail = coords.read(f)
        print("调用项目里真正的函数 眼\\coords.py::read()")
        print("→ 读出的坐标：%s" % (xy,))
        print("→ 过程明细（每一组认成什么、把握多少）：")
        for 文本, v in detail:
            print("     %-12s %.3f" % (文本, v))
        print()
        print("★ 两行数字分两组：第一组是 x，第二组是 y。")
        print("★ 每一组都要整体过门槛；有一个字没把握，整次就返回 None —— 宁可读不到，不许猜。")

    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
