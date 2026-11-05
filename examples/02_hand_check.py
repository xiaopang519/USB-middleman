# -*- coding: utf-8 -*-
"""example 02 —— 手：连上代理，读它自己的状态。只读。

只发 status / diag，都不在"会动东西"的命令里，所以不碰安全开关。

    python examples/02_hand_check.py
    python examples/02_hand_check.py B      # 只查 B

看什么：
    v=18     代理协议版本
    mode=hid 走的是真 HID（不是软件注入）
    inj=1    板子能注入
    pxpc     这颗板子的 px/count —— 每 1 个鼠标计数走多少像素
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "手"))

import hand_client as hc  # noqa: E402


def 查一个(cfg: dict, node: str) -> bool:
    host, port = hc.node_addr(cfg, node)
    print("── %s  %s:%d" % (node, host, port))
    try:
        with hc.Hand(host, port, timeout=4.0) as h:
            st = h.status()
            if not st.get("ok"):
                print("   status 不正常：%s" % st.get("raw"))
                return False
            print("   版本 %s  模式 %s  注入 %s  按键 %s  已运行 %s  px/count %s"
                  % (st.get("v"), st.get("mode"), st.get("inj"), st.get("btn"), st.get("up"), st.get("pxpc")))
            if st.get("mode") != "hid":
                print("   ⚠ 不是 hid 模式：板子可能没被认成 HID 设备，或固件不对")
            if str(st.get("pxpc") or "") and float(st.get("pxpc")) < 1.0:
                print("   ⚠ px/count 异常偏小，先跑 手/标定.py 重量")
            d = h.diag()
            if "raw" not in d:
                print("   diag：board=%s mode=%s ver=%s inject=%s"
                      % (d.get("board_found"), d.get("mode"), d.get("ver"), d.get("inject")))
        return True
    except hc.HandBusy:
        print("   板子被别的客户端占着（单客户端铁律）—— 把那个进程停掉，别同时开两个")
    except hc.HandError as exc:
        print("   连不上：%s" % exc)
        print("   排查：① 这台的代理起了吗 ② 8300 通不通 ③ 地址写对了吗")
    except Exception as exc:                      # noqa: BLE001
        print("   出错：%r" % (exc,))
    return False


def main() -> int:
    cfg = hc.load_cfg()
    节点 = sys.argv[1].upper() if len(sys.argv) > 1 else "A"
    好 = 查一个(cfg, 节点)
    print("\n结论：", "手是活的，可以进入 examples/03" if 好 else "先把手弄通，别急着动")
    return 0 if 好 else 1


if __name__ == "__main__":
    raise SystemExit(main())
