# -*- coding: utf-8 -*-
"""第 8 课的教学小工具 —— 让你**看见** A* 是怎么在壁障图上找路的。

这一课只有一句话：
    每次从"待办"里挑一个格子来扩展，挑的标准是 —— 已经走了几步 + 大概还要几步，谁小先扩谁。

用法（在项目根目录）：
    python 教学\\第8课.py 地图 63 116 9
    python 教学\\第8课.py 找路 63 116 57 108
    python 教学\\第8课.py 堵 62 116
"""
from __future__ import annotations

import heapq
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "脑"))

import 地图 as 项目地图                             # noqa: E402

MAP = "m100"


def 网格():
    return 项目地图.载入(MAP).copy()               # True = 可走


def 估价(a, b) -> int:
    """还能走几步的**估计**（曼哈顿距离：横着差多少 + 竖着差多少）。"""
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def 找路(w, 起, 终, 额外代价: dict | None = None):
    """教科书版 A*，30 行。和项目里的 地图.找路 是同一个东西。"""
    额外代价 = 额外代价 or {}
    待办 = [(估价(起, 终), 0, 起)]                 # (f=已走+估计, g=已走, 格子)
    来路, 已花 = {}, {起: 0}
    扩过 = set()
    while 待办:
        f, g, 当前 = heapq.heappop(待办)
        if 当前 in 扩过:
            continue
        扩过.add(当前)
        if 当前 == 终:
            路 = [当前]
            while 路[-1] in 来路:
                路.append(来路[路[-1]])
            return list(reversed(路)), len(扩过)
        x, y = 当前
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):     # 四方向
            n = (x + dx, y + dy)
            if not (0 <= n[0] < w.shape[0] and 0 <= n[1] < w.shape[1]):
                continue
            if not w[n]:
                continue
            ng = g + 1 + 额外代价.get((x, y, dx, dy), 0)
            if ng < 已花.get(n, 10 ** 9):
                已花[n] = ng
                来路[n] = 当前
                heapq.heappush(待办, (ng + 估价(n, 终), ng, n))
    return None, len(扩过)


def 画(w, 起, 终, 路=None, 半径=0) -> str:
    点 = set(路 or [])
    if 半径:
        x0 = max(0, min(起[0], 终[0]) - 半径)
        x1 = min(w.shape[0], max(起[0], 终[0]) + 半径 + 1)
        y0 = max(0, min(起[1], 终[1]) - 半径)
        y1 = min(w.shape[1], max(起[1], 终[1]) + 半径 + 1)
    else:
        x0, y0, x1, y1 = 0, 0, w.shape[0], w.shape[1]
    行s = []
    for y in range(y0, y1):
        行 = ""
        for x in range(x0, x1):
            c = (x, y)
            if c == 起:
                行 += "@"
            elif c == 终:
                行 += "X"
            elif c in 点:
                行 += "*"
            else:
                行 += "#" if w[x, y] else "."
        行s.append("%4d %s" % (y, 行))
    头 = "     " + "".join(str((x // 10) % 10) for x in range(x0, x1))
    头2 = "     " + "".join(str(x % 10) for x in range(x0, x1))
    return "\n".join([头, 头2] + 行s)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    命令 = sys.argv[1]
    w = 网格()

    if 命令 == "地图":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        r = int(sys.argv[4]) if len(sys.argv) > 4 else 9
        print("m100 局部（#可走  .墙  @你  X目标）")
        print(画(w, (x, y), (x, y), 半径=r))
        print()
        print("★ 一个格子只有 4 个邻居（上右下左）。斜着走不算一步。")

    elif 命令 == "找路":
        起 = (int(sys.argv[2]), int(sys.argv[3]))
        终 = (int(sys.argv[4]), int(sys.argv[5]))
        if not w[起]:
            print("起点是墙：", 起)
            return 1
        if not w[终]:
            print("终点是墙：", 终)
            return 1
        路, 扩 = 找路(w, 起, 终)
        项目路 = 项目地图.找路(MAP, 起, 终)
        if not 路:
            print("走不通")
            return 1
        print("起点 %s → 终点 %s" % (起, 终))
        print("A* 找到 %d 步，过程中一共扩展了 %d 个格子" % (len(路) - 1, 扩))
        print("项目里的 地图.找路 给出 %d 步 —— 两者%s"
              % (项目路.get("步数"), "一致" if 项目路.get("步数") == len(路) - 1 else "不一致（要查）"))
        print()
        print(画(w, 起, 终, 路, 半径=4))
        print()
        print("路径前 8 步：", 路[:8])

    elif 命令 == "堵":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        起, 终 = (63, 116), (57, 108)
        路0, _ = 找路(w, 起, 终)
        if not 路0:
            print("原路就不通")
            return 1
        w[x, y] = False                            # 把这一格当墙
        路1, 扩 = 找路(w, 起, 终)
        print("把 %s 堵上（假装那里是墙）" % ((x, y),))
        print("原来 %d 步 → 现在 %s 步，扩展了 %d 个格子"
              % (len(路0) - 1, (len(路1) - 1) if 路1 else "走不通", 扩))
        print()
        print(画(w, 起, 终, 路1, 半径=4))
        print()
        print("★ 它会绕。绕多远，取决于墙摆在哪 —— 这就是“重新规划”的意思。")

    elif 命令 == "罚":
        # 不改地形，只给一条边加代价 —— 项目里 资料\边表.json 就是干这个
        起, 终 = (63, 116), (57, 108)
        路0, _ = 找路(w, 起, 终)
        if not 路0 or len(路0) < 3:
            print("路太短，换一条试试")
            return 1
        (ax, ay), (bx, by) = 路0[1], 路0[2]
        边 = (ax, ay, bx - ax, by - ay)
        路1, _ = 找路(w, 起, 终, 额外代价={边: 8})
        print("给这一步加代价 8（地形没变，只是“这条路我们走过没走成”）：", 边)
        print("原来 %d 步 → 现在 %d 步" % (len(路0) - 1, len(路1) - 1 if 路1 else -1))
        print()
        print("前 6 步对比：")
        print("   原来：", 路0[:6])
        print("   现在：", (路1 or [])[:6])
        print()
        print("★ 加代价 ≠ 判死刑：路更长了但它还留着 —— 万一别的路更糟，还能回来。")

    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
