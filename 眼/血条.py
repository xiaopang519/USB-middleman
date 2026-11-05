# -*- coding: utf-8 -*-
"""血条 —— 用颜色判「这一格上站着东西没有」。不需要神经网络。

为什么用血条而不是别的：
    这个客户端里，**每个可选中单位头顶都有一条血条**：怪红、英雄/宠物品红、自己绿。
    血条是细长条（高 2~14px），比"找怪"容易得多，也不受地形颜色影响。
    旧项目 2026-10-19 用同一套阈值把它当「是不是真怪」的硬判据（半只兽、空地没有条），
    并通过了实测 —— 所以这里原样搬阈值，**不自己发明**。

判占位的用法（这是本文件唯一目的）：
    点之前，看落点那一格**上方 15~140px** 有没有血条（x 容差 ±30）。
    有 → 那一格大概率站着怪/人，点下去是"打"不是"走" → 换方向。

已知边界（不许装作没有）：
    · 只有血条 → 只能判"有单位"，**判不出是怪还是人**（要分得清就得上模型，见阶段三）。
    · 血条会被别的特效盖住，短暂漏检；所以它只当"否决条件"用：
      宁可少走一格，也不往怪身上点。
"""
from __future__ import annotations

import cv2
import numpy as np

# 画面里不属于"世界"的区域：底部聊天/状态栏、左上头像、右侧面板
UI_MASK = {"y_from": 790, "corner": (120, 270), "x_from": 1770}

# 阈值：红条/品红/绿条，全部来自旧项目实测（眼睛链路.json + detect.py）
MASK_RED = lambda r, g, b: (r > 140) & (g < 110) & (b < 110) & (r > g + 60)
MASK_MAGENTA = lambda r, g, b: (r > 120) & (b > 120) & (g < 90)
MASK_GREEN = lambda r, g, b: (g > 130) & (r < 110) & (b < 110) & (g > r + 60)
# ★ 2026-10-26 12:4x：怪（满血）的血条是**青绿/青色**（RGB 约 0,220,210），
#   MASK_GREEN 要求 b<110，直接漏掉 —— 实测"画面里明明有两只怪，却一根都没找到"。
MASK_CYAN = lambda r, g, b: (g > 130) & (b > 130) & (r < 110) & (g > r + 60) & (b > r + 60)

BAR_MIN_W, BAR_MAX_W = 25, 170      # 一条血条的宽度范围
BAR_MIN_H, BAR_MAX_H = 2, 14        # 高度范围
BAR_MIN_AREA = 55

TOL_X = 30                          # 落点与血条中心的 x 容差
UP_FROM, UP_TO = 15, 140            # 血条在落点之上多少像素内算「这一格有人」


def _bars(frame, mask) -> list[dict]:
    b = frame[:, :, 0].astype(int)
    g = frame[:, :, 1].astype(int)
    r = frame[:, :, 2].astype(int)
    m = mask(r, g, b).astype(np.uint8)
    m[UI_MASK["y_from"]:, :] = 0
    m[:UI_MASK["corner"][0], :UI_MASK["corner"][1]] = 0
    m[:, UI_MASK["x_from"]:] = 0
    n, _lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for k in range(1, n):
        x, y, w, h, area = (int(v) for v in st[k][:5])
        if BAR_MIN_W <= w <= BAR_MAX_W and BAR_MIN_H <= h <= BAR_MAX_H and area >= BAR_MIN_AREA:
            out.append({"中心x": x + w // 2, "顶y": y, "宽": w, "高": h})
    return out


def 红条(frame) -> list[dict]:
    return _bars(frame, MASK_RED)


def 品红条(frame) -> list[dict]:
    return _bars(frame, MASK_MAGENTA)


def 绿条(frame) -> list[dict]:
    return _bars(frame, MASK_GREEN)


def 青条(frame) -> list[dict]:
    """青色血条 —— 怪**满血**时是这个颜色（实测 RGB 约 0,220,210）。
    红条只在怪掉血后才变红，所以只认红条会"看不见满血的怪"。
    """
    return _bars(frame, MASK_CYAN)


def 全部(frame) -> dict:
    """一次算出三类条，外加各自被 UI 遮罩排除的区域说明。"""
    return {"红": 红条(frame), "品红": 品红条(frame), "绿": 绿条(frame),
            "排除": UI_MASK}


def 格上有人(frame, x: int, y: int, tol_x: int = TOL_X,
            up=(UP_FROM, UP_TO), 条=None) -> dict:
    """落点 (x,y) 上方有没有血条 → 有就说有，并给出最近的那条。"""
    条 = 条 if 条 is not None else 全部(frame)
    best, bestd = None, 10 ** 9
    for kind, bars in (("红", 条["红"]), ("品红", 条["品红"])):
        for b in bars:
            d = abs(b["中心x"] - x)
            if d > tol_x:
                continue
            dy = y - b["顶y"]
            if not (up[0] <= dy <= up[1]):
                continue
            if d < bestd:
                bestd, best = d, {**b, "种类": kind, "距离px": d, "正上方px": dy}
    return {"有人": best is not None, "条": best,
            "说明": ("落点上方 %d~%d px 内有%s血条 → 这一格站着东西"
                     % (up[0], up[1], best["种类"])) if best else
                    "落点上方没有血条 → 这一格看着是空的"}


def 条上有名字(frame, bar: dict, pad: int = 45, up=(34, 2), thr: int = 195,
              最少: int = 18) -> dict:
    """血条上方有没有名字牌（近白像素）—— 这是**真条/假条**的判据。

    ★ 旧库实测（2026-10-19）：血渍、墙饰、装饰也会被红色掩码捡成"条"；
      真怪的条上方有名字字（近白像素 112~280），假的几乎全 0。
      所以判占位之前先过这一道，能把假候选剔掉。
    """
    cx, ty = int(bar["中心x"]), int(bar["顶y"])
    y0, y1 = max(0, ty - up[0]), max(0, ty - up[1])
    x0, x1 = max(0, cx - pad), min(frame.shape[1], cx + pad)
    reg = frame[y0:y1, x0:x1]
    if reg.size == 0:
        return {"有名字": False, "白像素": 0}
    n = int(((reg[:, :, 0] > thr) & (reg[:, :, 1] > thr) & (reg[:, :, 2] > thr)).sum())
    return {"有名字": n >= 最少, "白像素": n}


def 白名条(frame, bars=None) -> list[dict]:
    """上方有**白色名字**的红条 = 玩家（旧库：玩家白字、英雄/宠蓝字、怪彩色字）。

    ★ 2026-10-25 14:11 纠正过一次语义：一开始把"上方有白字"当成"真怪"，
      结果 61 条里只剩 15 条 —— 那 15 条其实是**玩家**。白字是玩家的判据，不是怪的。
    """
    bars = 红条(frame) if bars is None else bars
    return [{**b, "白像素": j["白像素"]} for b in bars
            for j in [条上有名字(frame, b)] if j["有名字"]]


def 怪候选(frame, bars=None) -> list[dict]:
    """红条 + 上方**没有**白字 = 怪的候选（上方空旷或彩色字）。

    这是"红条"这一步的最终产物：把玩家剔掉，剩下的交给模型确认。
    """
    bars = 红条(frame) if bars is None else bars
    out = []
    for b in bars:
        j = 条上有名字(frame, b)
        if not j["有名字"]:
            out.append({**b, "白像素": j["白像素"]})
    return out


def 真条(frame, bars=None) -> list[dict]:
    """兼容旧名：等价于 `怪候选`（红条里剔掉玩家）。"""
    return 怪候选(frame, bars)


if __name__ == "__main__":
    import json
    import sys

    for p in ("..",):
        sys.path.insert(0, p)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import eye                                      # noqa: E402

    cfg = eye.load_cfg()
    f = eye.grab(cfg)
    if f is None:
        print(json.dumps({"ok": False, "原因": "没有可用帧"}, ensure_ascii=False))
        sys.exit(1)
    cfg_e = cfg.get("眼") or {}
    ax, ay = cfg_e.get("脚底锚点", [960, 412])
    gw, gh = cfg_e.get("格", [72, 48])
    bars = 全部(f)
    out = {"条数": {k: len(v) for k, v in bars.items() if k != "排除"},
           "红条": bars["红"], "品红条": bars["品红"], "绿条": bars["绿"],
           "落点": {}}
    for d, (dx, dy) in {"东": (1, 0), "西": (-1, 0), "南": (0, 1), "北": (0, -1)}.items():
        out["落点"][d] = 格上有人(f, ax + dx * gw, ay + dy * gh, 条=bars)
    print(json.dumps(out, ensure_ascii=False, indent=1))
