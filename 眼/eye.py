# -*- coding: utf-8 -*-
"""眼 —— 只读采集卡吐出来的「最新一帧」。

为什么这么设计（三条都是实测付过代价的）：

1. **不能从 Python 起 ffmpeg**：任何由 Python 派生的进程都拿不到 DirectShow 设备
   （报 `Unable to BindToObject for USB3.0 Video`），而从 PowerShell 直接起就正常。
   所以抓帧永远是 `眼\\抓帧.ps1` 的活，本模块只读它写的那张图。

2. **绝不拿旧画面当现场**：帧龄超过 `最老帧龄秒` 就返回 None。
   宁可「看不见」，也不要拿三分钟前的画面做决策。

3. **半张图要治两头**：
   写侧 ffmpeg 加 `-atomic_writing 1`（写临时文件再 rename，读者按构造看不到截断帧）；
   读侧仍然重试 + 空文件（< 2KB）判「正在写」+ 共享冲突短退避。
   读侧兜底留着，是因为换台机器 / 换个人起 ffmpeg 就可能没带那个开关。
"""
from __future__ import annotations

import json
import os
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_PATH = os.path.join(ROOT, "配置.json")

MIN_BYTES = 2048          # 比这还小 = 文件正在被写
MAX_AGE_S = 1.5           # 采集 10fps，正常帧龄 < 0.2s；超过这个数就当没看见


# ★★★ 回放台接缝（2026-10-27 13:0x，作者：「建回放台」）★★★
#
# 为什么要有它：
#   以前所有判定都直接 `eye.grab()` 上真机 —— 怪物位置、朝向、墙、时间全在变，
#   同一件事量十几次能得到 2 格 / 1.35 格 / 4 格 / 6 格 和撞墙折返的假数据，
#   于是只能拿 1~3 个样本定规则，规则下次样本一变就推翻（"改这里错那里"）。
#
#   `设帧源(fn)` 让帧从**磁盘**来，判定逻辑一个字都不用改（`eye.grab()` 还是那个调用）。
#   这样改一行代码，就能在几十个录好的真实帧上 10 秒内验完有没有把别处弄坏。
_帧源 = None


def 设帧源(fn):
    """装一个从磁盘供帧的函数（回放/场景用）。传 None = 恢复真机采集。"""
    global _帧源
    _帧源 = fn


def 清帧源():
    global _帧源
    _帧源 = None


def 在用帧源() -> bool:
    return _帧源 is not None


# ────────────────────────────── 配置 ──────────────────────────────
def load_cfg(path: str | None = None) -> dict:
    """读 配置.json。只读一次也行，但这里每次都读 —— 改配置不用重启任何东西。"""
    with open(path or CFG_PATH, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def _eye(cfg: dict) -> dict:
    return cfg.get("眼") or {}


# ────────────────────────────── 帧龄 ──────────────────────────────
def frame_age(cfg: dict | None = None) -> float | None:
    """最新帧到现在多少秒。文件不在返回 None。

    ★ 负值当 0：2026-10-25 14:27 体检里出现过 `帧龄 -1.00s` ——
      ffmpeg 写文件的 mtime 比本机时钟快了约 1 秒（两边时钟粒度不同）。
      负的帧龄不是"更新鲜"，是**时钟不一致**，当 0 处理并在 health 里标出来，
      免得日志里出现让人怀疑读数的负数。
    """
    cfg = cfg or load_cfg()
    src = _eye(cfg).get("帧路径")
    if not src or not os.path.exists(src):
        return None
    try:
        age = time.time() - os.path.getmtime(src)
        return max(0.0, age)
    except OSError:
        return None


def alive(cfg: dict | None = None) -> bool:
    cfg = cfg or load_cfg()
    age = frame_age(cfg)
    limit = float(_eye(cfg).get("最老帧龄秒", MAX_AGE_S))
    return age is not None and age <= limit


# ────────────────────────────── 读帧 ──────────────────────────────
def read_bytes(cfg: dict | None = None) -> bytes | None:
    """把最新帧的原始字节读出来（不解码）。面板要显示「我现在看见了什么」时用。"""
    cfg = cfg or load_cfg()
    src = _eye(cfg).get("帧路径")
    if not src:
        return None
    for _k in range(4):                     # 写的人刚好在写 → 短退避
        try:
            with open(src, "rb") as fh:
                data = fh.read()
            return data if len(data) >= MIN_BYTES else None
        except (PermissionError, OSError):
            time.sleep(0.02 * (_k + 1))
    return None


def grab(cfg: dict | None = None, tries: int = 6, wait_s: float = 0.06):
    """抓一帧，返回 BGR ndarray；读不到 / 太旧 / 解码失败 → **None**（不猜）。

    ★★ 2026-10-26 22:4x：**改成从"持续采流"里取最后一帧**（作者 指出）。
      原来每叫一次就重读 2.8MB 文件 + 解 PNG = 36ms，而且各模块各抓各的：
      打怪里"看血量→悬停→按键→确认"每一步抓到的是不同时刻的画面，前后对不上，
      表现就是"打一下就不打了，因为不知道打哪里"。
      现在：`屏幕流` 一个线程常驻采帧（实测 10.8 fps，帧龄 0.09 秒），
      这里零成本拿到**同一帧**，所有消费者看到的是同一个现场。
    """
    cfg = cfg or load_cfg()
    limit = float(_eye(cfg).get("最老帧龄秒", MAX_AGE_S))

    # ⓪ 回放台：装了帧源就从磁盘取（判定逻辑完全不变）
    if _帧源 is not None:
        return _帧源()

    # ① 优先走持续流（零解码成本、全局同一帧）
    try:
        import 屏幕流
        if 屏幕流.启动(cfg):
            f, 龄 = 屏幕流.最新(最老秒=limit)
            if f is not None:
                return f
            # 流还没出帧（刚启动）→ 落回文件读，别干等
    except Exception:
        pass

    # ② 兜底：直接从文件读一帧（流不可用时）
    for _ in range(max(1, tries)):
        data = read_bytes(cfg)
        if data:
            try:
                import cv2
                frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
            except Exception:
                frame = None
            if frame is not None:
                age = frame_age(cfg)
                if age is not None and age > limit:
                    return None             # 图能解开，但它是旧的 → 当没看见
                return frame
        time.sleep(wait_s)
    return None


# ────────────────────────────── 体检 ──────────────────────────────
def health(cfg: dict | None = None) -> dict:
    """眼这一路的完整判据。面板和体检都吃这一份，不各算一套。"""
    cfg = cfg or load_cfg()
    spec = _eye(cfg)
    path = spec.get("帧路径")
    age = frame_age(cfg)
    limit = float(spec.get("最老帧龄秒", MAX_AGE_S))
    out = {
        "ok": False,
        "路径": path,
        "帧龄秒": None if age is None else round(age, 3),
        "最老帧龄秒": limit,
        "字节": None,
        "形状": None,
        "原因": "",
    }
    if age is None:
        out["原因"] = "帧文件不在 —— 抓帧没起（眼\\抓帧.ps1）或路径错了"
        return out
    try:
        out["字节"] = os.path.getsize(path)
    except OSError:
        pass
    out["帧龄秒"] = round(age, 3)
    if age > limit:
        out["原因"] = "帧太旧 %.1fs > %.1fs —— ffmpeg 掉了或卡住" % (age, limit)
        return out
    frame = grab(cfg, tries=2, wait_s=0.05)
    if frame is None:
        out["原因"] = "帧读不出来 / 解不开 —— 可能正在写，或 ffmpeg 参数不对"
        return out
    out["形状"] = [int(frame.shape[1]), int(frame.shape[0])]
    want = (int(spec.get("宽", 0)), int(spec.get("高", 0)))
    if want[0] and want[1] and (out["形状"][0], out["形状"][1]) != want:
        # 尺寸不符不是「死」，但一定要说出来：坐标换算全按这个尺寸算的
        out["原因"] = "尺寸 %dx%d 与配置 %dx%d 不符 —— 坐标换算会错" % (
            out["形状"][0], out["形状"][1], want[0], want[1])
        out["ok"] = True                    # 灯亮，但带警告
        out["警告"] = True
        return out
    out["ok"] = True
    return out


def coord(cfg: dict | None = None, frame=None) -> dict:
    """读画面左下角那行「地图名 X:Y」里的两个数。

    返回 {ok, x, y, 把握, 明细, 原因}。读不到就是 ok=False + 原因 —— 绝不猜一个坐标出来。
    """
    cfg = cfg or load_cfg()
    spec = _eye(cfg)
    out = {"ok": False, "x": None, "y": None, "把握": None, "明细": [], "原因": ""}
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in __import__("sys").path:
        __import__("sys").path.insert(0, here)
    try:
        import coords as _coords
    except Exception as exc:
        out["原因"] = "导不进 coords：%s" % exc
        return out
    if frame is None:
        frame = grab(cfg)
    if frame is None:
        out["原因"] = "没有可用帧（见 /health 里眼那盏灯）"
        return out
    box = tuple(spec.get("坐标行") or _coords.DEFAULT_BOX)
    glyph_dir = os.path.join(here, spec.get("字形目录", "字形"))
    # ★ 2026-10-26 16:2x：**永远走多框**（原来只在单框失败时才兜底 —— 不够）。
    #   今天那个 bug 是"窄框读出了一个错的数"（149:56 → 29:28），**不是返回 None**，
    #   所以"失败了才兜底"根本救不了它。多框取"数字位数最多"的那个，才挡得住裁位。
    xy, detail, 用了 = _coords.read_multi(frame, boxes=[box] + [b for b in _coords.候选框 if b != box],
                                          glyph_dir=glyph_dir)
    if 用了 is not None:
        out["用了框"] = list(用了)
    out["明细"] = detail
    out["裁切区"] = list(box)
    if xy is None:
        out["原因"] = "认不出来（明细见 明细 字段）"
        return out
    out.update({"ok": True, "x": int(xy[0]), "y": int(xy[1]),
                "把握": float(detail[-1][1]) if detail else None})
    return out


if __name__ == "__main__":
    import sys
    try:                          # 输出统一 UTF-8：不然管道/别的终端按 GBK 读成乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(health(), ensure_ascii=False, indent=1))
