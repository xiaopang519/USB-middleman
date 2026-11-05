# -*- coding: utf-8 -*-
"""版面审计 —— 在没有渲染器的环境里，用几何 + 文字量估算检查 PPT 会不会出界/挤爆。"""
from __future__ import annotations

import math
import os
import sys

from pptx import Presentation
from pptx.util import Emu

HERE = os.path.dirname(os.path.abspath(__file__))
WIN, HIN = 13.333, 7.5


def 英寸(v) -> float:
    return float(Emu(v).inches) if v is not None else 0.0


def main(path: str) -> int:
    prs = Presentation(path)
    问题 = []
    print("幻灯片 %d 张  尺寸 %.2f x %.2f 英寸" % (len(prs.slides), WIN, HIN))
    for i, s in enumerate(prs.slides, 1):
        形状 = []
        for sh in s.shapes:
            l, t = 英寸(sh.left), 英寸(sh.top)
            w, h = 英寸(sh.width), 英寸(sh.height)
            形状.append("%s(%.1f,%.1f %.1fx%.1f)" % (sh.shape_type, l, t, w, h))
            if l + w > WIN + 0.05 or t + h > HIN + 0.05:
                问题.append("第%d页 形状出界：%.1f+%.1f / %.1f+%.1f" % (i, l, w, t, h))
            if l < -0.02 or t < -0.02:
                问题.append("第%d页 形状在页面外：l=%.2f t=%.2f" % (i, l, t))
            if sh.has_text_frame and sh.text_frame.text.strip():
                文本 = sh.text_frame.text
                字号 = 18.0
                号s = [float(r.font.size.pt)
                       for p in sh.text_frame.paragraphs for r in p.runs if r.font.size]
                if 号s:
                    字号 = max(号s)
                # 估算：一个汉字约占字号 1.0 倍宽，行高 1.35 倍
                每行字 = max(1, int(w * 72 / 字号))
                行数 = sum(max(1, math.ceil(len(p.text) / 每行字))
                          for p in sh.text_frame.paragraphs if p.text) or 1
                需要 = 行数 * 字号 * 1.35 / 72
                if 需要 > h + 0.08:
                    问题.append("第%d页 文字可能溢出：需 %.2f 英寸 / 框 %.2f 英寸（%d 行，%d 字/行）"
                                % (i, 需要, h, 行数, 每行字))
            if sh.shape_type == 13:      # PICTURE
                if w < 1.0 or h < 1.0:
                    问题.append("第%d页 图片过小 %.2fx%.2f" % (i, w, h))
        print("  %2d. %s" % (i, " | ".join(形状)))
    print()
    if 问题:
        print("发现 %d 个可能的问题：" % len(问题))
        for q in 问题:
            print("  -", q)
        return 1
    print("版面审计通过：无出界、无明显文字溢出。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "阈值演示.pptx")))
