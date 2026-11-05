# -*- coding: utf-8 -*-
"""第 5 课的教学小工具 —— 从"屏幕上一个点"到"手上真的移动多少"。

三个空间，一条换算链：

    采集画面 (1920×1080)   ×1.6667   →   B 机桌面 (3200×1800)   →   板子发多少计数
         脚底锚点 (960,412)              点到的桌面坐标               px ÷ px_per_count

用法：
    python 教学\\第5课.py 空间
    python 教学\\第5课.py 算 960 412
    python 教学\\第5课.py 格子
    python 教学\\第5课.py 光标
    python 教学\\第5课.py 标图
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (os.path.join(ROOT, "眼"), os.path.join(ROOT, "手"), os.path.join(ROOT, "脑")):
    sys.path.insert(0, p)

import eye                                        # noqa: E402


def 读(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def 常数():
    cfg = eye.load_cfg()
    spec = cfg.get("眼") or {}
    return cfg, spec, {
        "画面宽": int(spec.get("宽", 1920)), "画面高": int(spec.get("高", 1080)),
        "缩放": float(spec.get("缩放", 1.6667)),
        "锚点": tuple(spec.get("脚底锚点") or (960, 412)),
        "格": tuple(spec.get("格") or (72, 48)),
        "服务": spec.get("眼睛服务") or "", "令牌": spec.get("眼睛令牌") or "",
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    命令 = sys.argv[1]
    cfg, spec, K = 常数()
    桌面宽 = int(round(K["画面宽"] * K["缩放"]))
    桌面高 = int(round(K["画面高"] * K["缩放"]))

    if 命令 == "空间":
        print("三个空间，一换算就通：")
        print()
        print("① 采集画面    %d × %d      ← 眼看到的世界" % (K["画面宽"], K["画面高"]))
        print("② B 机桌面    %d × %d      ← 手要点的世界" % (桌面宽, 桌面高))
        print("③ 场景世界格  (x, y)        ← 壁障图的世界")
        print()
        print("换算系数 = %d ÷ %d = %.4f" % (桌面宽, K["画面宽"], K["缩放"]))
        print("        = %d ÷ %d = %.4f  ← 两个方向算出来一样，说明等比缩放"
              % (桌面高, K["画面高"], 桌面高 / K["画面高"]))
        print()
        print("所以：桌面坐标 = 画面坐标 × %.4f" % K["缩放"])
        print("      画面坐标 = 桌面坐标 ÷ %.4f" % K["缩放"])

    elif 命令 == "算":
        if len(sys.argv) < 4:
            print("用法：python 教学\\第5课.py 算 960 412")
            return 2
        x, y = int(sys.argv[2]), int(sys.argv[3])
        X, Y = x * K["缩放"], y * K["缩放"]
        print("画面 (%d, %d)  →  桌面 (%.1f, %.1f)" % (x, y, X, Y))
        print("计算过程：%d × %.4f = %.1f      %d × %.4f = %.1f"
              % (x, K["缩放"], X, y, K["缩放"], Y))
        print()
        print("落点必须是整数 —— 光标不能停在半个像素上：")
        print("  int(%.1f) = %d，四舍五入 = %d" % (X, int(X), round(X)))
        print()
        print("★ 为什么还要锚点？因为角色永远在画面 (%d, %d)，"
              % K["锚点"])
        print("  所以'往东走一格'= 锚点 + 格宽 → 画面 (%d, %d) → 桌面 (%.0f, %.0f)"
              % (K["锚点"][0] + K["格"][0], K["锚点"][1],
                 (K["锚点"][0] + K["格"][0]) * K["缩放"], K["锚点"][1] * K["缩放"]))

    elif 命令 == "格子":
        ax, ay = K["锚点"]
        gw, gh = K["格"]
        print("锚点（角色脚底）：画面 (%d, %d) → 桌面 (%.0f, %.0f)"
              % (ax, ay, ax * K["缩放"], ay * K["缩放"]))
        print()
        print("%-6s %-6s %-18s %-20s %s"
              % ("方向", "格数", "画面落点", "桌面落点", "为什么"))
        行 = [("东", 1, (1, 0), "一格就够"), ("西", 1, (-1, 0), "一格就够"),
              ("南", 2, (0, 1), "一格会点在角色身上"), ("北", 2, (0, -1), "一格会点在角色身上")]
        for 名, n, (dx, dy), 注 in 行:
            px, py = ax + dx * gw * n, ay + dy * gh * n
            print("%-6s %-6d (%4d,%4d)         (%6.0f,%6.0f)        %s"
                  % (名, n, px, py, px * K["缩放"], py * K["缩放"], 注))
        print()
        print("★ 南北要用两格：角色精灵约 40×90 像素，一格（±48）正压在自己身上，")
        print("  点下去等于点自己，什么也不会发生。这是实测踩出来的，不是猜的。")

    elif 命令 == "光标":
        import 动作
        cur = 动作.读光标(K["服务"], K["令牌"])
        ax, ay = K["锚点"]
        if not cur.get("ok"):
            print("读不到 B 机光标：%s" % (cur.get("原因") or cur))
            print("（B 机眼睛服务没开？这一条不影响本课其它内容）")
            return 1
        print("B 机真实光标：桌面 (%d, %d)" % (cur["x"], cur["y"]))
        画x, 画y = cur["x"] / K["缩放"], cur["y"] / K["缩放"]
        print("换算回画面：  (%.1f, %.1f)" % (画x, 画y))
        print("脚底锚点在画面：(%d, %d) → 桌面 (%.0f, %.0f)"
              % (ax, ay, ax * K["缩放"], ay * K["缩放"]))
        dx, dy = cur["x"] - ax * K["缩放"], cur["y"] - ay * K["缩放"]
        print()
        print("光标离锚点的桌面距离：(%+.0f, %+.0f) px" % (dx, dy))
        print("→ 要让光标到锚点，板子要发 %.0f, %.0f 个计数（除以 px_per_count）"
              % (dx / 2.49, dy / 2.49))
        print()
        print("★ 三句话：算得出 → 发得准 → 点得下。任何一环不准，就会点到别的地方。")

    elif 命令 == "标图":
        cfg = eye.load_cfg()
        f = eye.grab(cfg)
        if f is None:
            print("没有可用帧")
            return 1
        画 = f.copy()
        ax, ay = K["锚点"]
        gw, gh = K["格"]
        cv2.drawMarker(画, (ax, ay), (0, 255, 0), cv2.MARKER_CROSS, 30, 2)
        cv2.putText(画, "anchor", (ax + 12, ay - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        for dx, dy in ((1, 0), (-1, 0), (0, 2), (0, -2), (1, 1), (-1, -1)):
            px, py = ax + dx * gw, ay + dy * gh
            cv2.drawMarker(画, (px, py), (0, 200, 255), cv2.MARKER_TILTED_CROSS, 14, 1)
        存 = os.path.join(ROOT, "证据", "教学_锚点与格.png")
        ok, buf = cv2.imencode(".png", 画)
        buf.tofile(存)
        print("绿十字 = 脚底锚点 (%d, %d)" % (ax, ay))
        print("橙叉   = 六个方向的落点（东西一格，南北两格）")
        print("存成：%s" % 存)

    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
