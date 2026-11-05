# -*- coding: utf-8 -*-
"""存fixture.py —— 给"读坐标"存正样本和负样本，供自检用。

为什么（2026-10-26 15:3x，从 game-bot-forge 的纪律里拿的逻辑）：
    今天那个 bug —— 坐标行 `地牢一层东 149:56` 被固定框裁掉右半截、
    读成 `29:28`，看起来就像角色瞬移。这个错误**不报错、不返回 None**，
    它给你一个"看起来完全合法"的数字，然后毒死认图/A*/打怪判据。
    肉眼比对截图才发现。

    防它的办法不是"更小心"，是**一条必须读不出来的样本**：
      · 正样本：这一块该读出 (149,56)
      · 负样本：把数字切掉一半的那一块，**必须返回 None**
        如果哪天它从那块里"猜"出一个数，自检立刻红。

用法：
    python 工具\\存fixture.py          # 用当前画面重新生成两个样本
    python 工具\\存fixture.py --看     # 只打印现有的样本
"""
from __future__ import annotations

import json
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for 子 in ("眼", "手", "脑"):
    sys.path.insert(0, os.path.join(ROOT, 子))

import coords  # noqa: E402
import eye  # noqa: E402

目录 = os.path.join(ROOT, "证据", "fixtures")
清单 = os.path.join(目录, "坐标_样本.json")


def _存(路径: str, 图: np.ndarray):
    ok, buf = cv2.imencode(".png", 图)
    if not ok:
        raise RuntimeError("编码失败：%s" % 路径)
    with open(路径, "wb") as fh:
        fh.write(buf.tobytes())


def 生成() -> dict:
    os.makedirs(目录, exist_ok=True)
    cfg = eye.load_cfg()
    f = eye.grab(cfg)
    if f is None:
        raise SystemExit("没有可用帧（眼睛没开？）")
    box = coords.DEFAULT_BOX
    x0, y0, x1, y1 = box
    正 = f[y0:y1, x0:x1]
    xy, det = coords.read(f)
    if not xy:
        raise SystemExit("当前这一帧读不出坐标，没法存正样本（明细：%s）" % (det,))

    # 负样本：把正样本**右半截切掉**，模拟"数字被裁掉"——这种图必须读不出来
    负 = 正[:, : int(正.shape[1] * 0.55)]

    正路径 = os.path.join(目录, "坐标_正.png")
    负路径 = os.path.join(目录, "坐标_负_被裁掉.png")
    _存(正路径, 正)
    _存(负路径, 负)
    数 = {
        "正": {"路径": os.path.relpath(正路径, ROOT), "期望": [int(xy[0]), int(xy[1])],
               "说明": "这一块该读出坐标"},
        "负": {"路径": os.path.relpath(负路径, ROOT), "期望": None,
               "说明": "数字被裁掉一半，必须读不出来（返回 None），读出任何数都是 bug"},
        "框": list(box),
    }
    with open(清单, "w", encoding="utf-8") as fh:
        json.dump(数, fh, ensure_ascii=False, indent=1)
    return 数


def 读清单() -> dict:
    if not os.path.exists(清单):
        return {}
    with open(清单, "r", encoding="utf-8") as fh:
        return json.load(fh)


def 自检() -> dict:
    """跑正负两个样本。返回 {ok, 项:[...]}。"""
    数 = 读清单()
    if not 数:
        return {"ok": False, "项": [{"名": "样本存在", "ok": False, "说明": "还没生成，跑 python 工具\\存fixture.py"}]}
    项 = []
    for 名 in ("正", "负"):
        条 = 数[名]
        p = os.path.join(ROOT, 条["路径"])
        if not os.path.exists(p):
            项.append({"名": 名, "ok": False, "说明": "文件没了：%s" % p})
            continue
        图 = cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)
        # ★ 样本本身就是裁好的小块，必须用整张图当框；
        #   否则 read() 会拿 DEFAULT_BOX 再切一次，切到图外面去 → 永远读不出（实测）。
        xy, det = coords.read(图, box=(0, 0, 图.shape[1], 图.shape[0]))
        期望 = 条["期望"]
        if 期望 is None:
            好 = xy is None
            说明 = "应该读不出，实际 %s" % (xy,)
        else:
            好 = xy is not None and [int(xy[0]), int(xy[1])] == list(期望)
            说明 = "期望 %s，实际 %s" % (期望, xy)
        项.append({"名": 名, "ok": 好, "说明": 说明})
    return {"ok": all(x["ok"] for x in 项), "项": 项}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json
    if "--看" in sys.argv:
        print(json.dumps(读清单(), ensure_ascii=False, indent=1))
        return 0
    数 = 生成()
    print(json.dumps(数, ensure_ascii=False, indent=1))
    r = 自检()
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
