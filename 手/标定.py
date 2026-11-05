# -*- coding: utf-8 -*-
"""标定 —— 量出「板子发 1 个鼠标计数 = 屏幕上走多少像素」。

为什么必须量、不能写死：
    这块板子伪装成罗技鼠标，它发的相对位移会先经过 Windows 的「指针速度」再变成像素。
    换机器、换灵敏度、换 DPI缩放，比例就变。历史上 B 机实测 2.5，另一个地方写 1.0，
    差了 2.5 倍 —— 用它算落点会整片打偏。所以：**量，然后落盘**。

为什么不由代理（hand_agent）自己标定：
    代理那份 do_calibrate 要借「眼睛服务」(127.0.0.1:18790) 读光标，因为代理常以服务方式
    跑在会话 0，GetCursorPos 读到的是会话 0 的桌面。但本程序跑在**用户会话**里，
    GetCursorPos 就是真光标 —— 不需要借任何东西。所以标定放在这边做，
    结果写 `手\\标定.json`；脑换算成计数后用 `move <计数>` 发命令，
    **永远不走 `movepx`**（那条路用的是代理自己的标定，可能是错的）。

用法：
    python 手\\标定.py            # 默认标 A
    python 手\\标定.py B          # 标 B（要那块板空着）
    python 手\\标定.py A --counts 300 --samples 5

退出码：0 标定成功；1 量不出来（人正在用鼠标 / 板子不回 / 屏边夹住）；2 自己出错。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import hand_client as hc               # noqa: E402

CALIB_PATH = os.path.join(HERE, "标定.json")
_u = ctypes.WinDLL("user32", use_last_error=True)


def cursor() -> tuple[int, int]:
    p = wt.POINT()
    _u.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def cursor_remote(base: str, token: str = "", timeout: float = 3.0) -> tuple[int, int] | None:
    """读**另一台机器**的光标（B 机眼睛服务的 /cursor）。读不到返回 None。

    ★ 只读。眼睛服务的 /move 在 B 上返回 `SetCursorPos(1720,687) -> False`（2026-10-25 实测），
      /cursor 读是好的 —— 所以它只能当尺子，不能当手。手永远是板子。
    ⚠️ 2026-10-26 复核：原先这里写的「被 UIPI 拒」**不成立** —— A 机实测 `SetCursorPos`
      返回 1、光标真的动（UIPI 拦的是发消息/注入输入，且失败一般给 err=5，不是 err=0）。
      B 上那条 False 的真实原因**未决**。见 `证据\\复核_UIPI归因_20261026_0140.json`。
    """
    import urllib.request
    if not base:
        return None
    try:
        req = urllib.request.Request(base.rstrip("/") + "/cursor",
                                     headers={"X-Token": token} if token else {})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        c = d.get("cursor") or {}
        return int(c["x"]), int(c["y"])
    except Exception:
        return None


def cursor_for(node: str, cfg: dict):
    """A 机用自己的真光标；别的机器用眼睛服务读。"""
    if node.upper() == "A":
        return cursor
    spec = cfg.get("眼") or {}
    base, token = spec.get("眼睛服务") or "", spec.get("眼睛令牌") or ""

    def _r():
        return cursor_remote(base, token)
    return _r


def park(x: int, y: int):
    """把光标挪到一个「往右走 500px 也不会撞墙」的位置。

    ★ 只说清楚一件事：SetCursorPos 只用来**摆位**。位移量一律来自板子那一下
      （读数 p0 与 p1 都是走位之后量的），所以测出来的比例是板子的，不是摆位的。
    """
    _u.SetCursorPos(int(x), int(y))
    time.sleep(0.05)


def measure(node: str, counts: int, samples: int) -> dict:
    """量 px/count。

    ★ 2026-10-25 13:30 实测：在这台机器上 **SetCursorPos 无声失败**
      （返回 0、errno 0、光标一动不动）—— 目标进程是 High 完整性，我们在 Medium，
      摆位这条路走不通。所以这里**完全不摆位**：
        1. 先看光标安不安静（连续两次读数一致）→ 有人在用就作废这一轮；
        2. 右边空间够就往右量，不够就往左量（避免撞屏幕边界被夹住）；
        3. 位移一律来自板子的 inject，读数用 GetCursorPos。
    """
    cfg = hc.load_cfg()
    host, port = hc.node_addr(cfg, node)
    ratios, log = [], []
    screen_w = _u.GetSystemMetrics(0)
    get_cursor = cursor_for(node, cfg)

    with hc.Hand(host, port) as hand:
        for i in range(samples):
            p0a = get_cursor()
            if p0a is None:
                log.append({"i": i, "作废": "读不到光标（眼睛服务 /cursor 不通）"})
                continue
            time.sleep(0.06)
            p0b = get_cursor()
            if p0b is None or p0a != p0b:
                log.append({"i": i, "作废": "光标在动（人正在用鼠标）", "p0": p0a, "p0b": p0b})
                continue
            p0 = p0b
            # 两个方向都量，取 |dx| 大的那个：撞到屏幕边会被夹住，夹住的那次一定偏小。
            row, best = {"i": i, "p0": p0, "试": {}}, 0
            for dirn in (1, -1):
                reply = hand.cmd("move %d 0" % (counts * dirn))
                time.sleep(0.12)
                p1 = get_cursor()
                if p1 is None:
                    row["试"][str(dirn)] = "移动后读不到光标"
                    continue
                dx = (p1[0] - p0[0]) * dirn
                row["试"][str(dirn)] = {"回执": reply, "p1": p1, "dx": dx,
                                        "比例": round(dx / float(counts), 4)}
                if reply.startswith("ERR"):
                    row["注"] = reply
                    break
                if dx > best:
                    best = dx
                    row["方向"] = "+" if dirn > 0 else "-"
            if row.get("注"):
                log.append(row)
                if not row["注"].startswith("ERR yield"):
                    return {"ok": False, "原因": "板子拒了：%s" % row["注"], "明细": log}
                continue
            log.append(row)
            if best > 0:
                ratios.append(best / float(counts))

    if not ratios:
        return {"ok": False,
                "原因": "一次有效样本都没有（多半是真人正在用鼠标 → 体面的让路；或光标卡在屏幕边）",
                "明细": log}
    med = statistics.median(ratios)
    spread = max(ratios) - min(ratios)
    return {"ok": True, "px_per_count": round(med, 4),
            "样本": [round(r, 4) for r in ratios],
            "极差": round(spread, 4),
            # 只有一个样本时「极差 0」没有意义 —— 至少要 3 个才算数
            "可信": (len(ratios) >= 3) and spread <= max(0.15, med * 0.06),
            "node": node, "counts": counts, "明细": log}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("node", nargs="?", default="A")
    ap.add_argument("--counts", type=int, default=200)
    ap.add_argument("--samples", type=int, default=3)
    a = ap.parse_args()

    res = measure(a.node.upper(), a.counts, a.samples)
    if not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False, indent=1))
        print("\n没标成。判据都写在上面，别猜比例去用。")
        return 1

    out = {"node": res["node"], "px_per_count": res["px_per_count"],
           "方法": "相对位移 / 计数，取中位数（在用户会话里直接读真光标）",
           "样本": res["样本"], "极差": res["极差"], "可信": res["可信"],
           "时间": time.strftime("%Y-%m-%dT%H:%M:%S")}
    # 一台机器一条：A、B 的指针速度可以不一样，绝不能互相覆盖
    all_ = {}
    try:
        with open(CALIB_PATH, "r", encoding="utf-8-sig") as fh:
            all_ = json.load(fh) or {}
    except Exception:
        all_ = {}
    nodes = all_.get("nodes") if isinstance(all_.get("nodes"), dict) else {}
    if isinstance(all_.get("px_per_count"), (int, float)):      # 老格式（单节点）→ 迁进来
        nodes.setdefault(all_.get("node", "A"), {"px_per_count": all_["px_per_count"]})
    nodes[res["node"]] = out
    all_ = {"nodes": nodes, "_说明": "每台机器一条。脑换算一律查这里，不用代理自己的标定。"}
    with open(CALIB_PATH, "w", encoding="utf-8") as fh:
        json.dump(all_, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print("\n写入 %s" % CALIB_PATH)
    if not res["可信"]:
        print("⚠ 极差 %.3f 偏大：换机器/改了指针速度，或者中途有人碰了鼠标。建议重跑。"
              % res["极差"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
