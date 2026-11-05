# -*- coding: utf-8 -*-
"""生成《阈值是什么》动画PPT —— 第2课的看图版。

只用 numpy + Pillow + python-pptx（不依赖 cv2），图全部取自项目里的真帧。
产出：
    教学\\阈值演示.pptx      ← 主交付，翻页 + 一页自动播放的动图
    教学\\预览\\*.png        ← 渲染检查用
"""
from __future__ import annotations

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
出 = os.path.join(HERE, "阈值素材")
os.makedirs(出, exist_ok=True)

帧路径 = os.path.join(ROOT, "资料", "样本", "旧_yolo预演_171706.png")
if not os.path.exists(帧路径):
    帧路径 = os.path.join(ROOT, "证据", "现场帧_1310.png")

PASS_R, PASS_G, PASS_B, PASS_D = 140, 110, 110, 60


def 蒙版(arr, thr):
    R = arr[:, :, 0].astype(int)
    G = arr[:, :, 1].astype(int)
    B = arr[:, :, 2].astype(int)
    return (R > thr) & (G < PASS_G) & (B < PASS_B) & (R > G + PASS_D)


def 着色(arr, thr, 亮=1.0):
    """过关的点保留原色（提亮一点），没过的压成暗灰 —— 让"电脑看到的红"一眼可见。"""
    m = 蒙版(arr, thr)
    gray = (arr.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32))
    灰 = (np.dstack([gray] * 3) * 0.22).astype(np.uint8)
    out = 灰.copy()
    if m.any():
        强 = np.clip(arr[m].astype(np.float32) * 亮 + 30, 0, 255).astype(np.uint8)
        out[m] = 强
    return out, int(m.sum())

def 取字体(号):
    for p in (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\simhei.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, 号)
            except Exception:
                pass
    return ImageFont.load_default()


def 带字(img: Image.Image, 大字: str, 小字: str = "") -> Image.Image:
    im = img.convert("RGB")
    d = ImageDraw.Draw(im)
    w, h = im.size
    框 = int(h * 0.16)
    d.rectangle([0, 0, w, 框], fill=(0, 0, 0))
    d.text((18, int(框 * 0.12)), 大字, font=取字体(int(h * 0.105)), fill=(255, 255, 255))
    if 小字:
        d.text((18, int(框 * 0.62)), 小字, font=取字体(int(h * 0.055)), fill=(255, 215, 120))
    return im


def main() -> int:
    原全 = Image.open(帧路径).convert("RGB")
    全arr = np.array(原全)                      # ★ 计数一律在**原分辨率**上算，
                                               #   这样和 `python 教学\第2课.py 数红 N` 出来的数完全一致
    宽缩 = 1280
    原 = 原全.resize((宽缩, int(原全.height * 宽缩 / 原全.width)), Image.LANCZOS)
    arr = np.array(原)

    阶梯 = [0, 100, 140, 200, 254, 255]
    数 = {}
    for t in 阶梯:
        图, _ = 着色(arr, t)
        数[t] = int(蒙版(全arr, t).sum())
        Image.fromarray(图).save(os.path.join(出, "阈值_%03d.png" % t))

    # ── 动图：阈值从 0 扫到 255（数字取原分辨率的真实计数）──
    帧s = []
    for t in range(0, 256, 5):
        图, _ = 着色(arr, t)
        n = int(蒙版(全arr, t).sum())
        im = Image.fromarray(图).resize((720, int(720 * 图.shape[0] / 图.shape[1])), Image.LANCZOS)
        im = 带字(im, "阈值 = %d" % t, "被判为红色的点：%d 个" % n)
        帧s.append(im.convert("P", palette=Image.ADAPTIVE, colors=128))
    gif = os.path.join(出, "阈值扫描.gif")
    帧s[0].save(gif, save_all=True, append_images=帧s[1:], duration=110, loop=0, optimize=True)

    # ── PPT ──
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    空白 = prs.slide_layouts[6]
    黑 = RGBColor(0x1A, 0x1A, 0x1A)
    深 = RGBColor(0x44, 0x44, 0x44)
    红 = RGBColor(0xC0, 0x39, 0x2B)
    绿 = RGBColor(0x1E, 0x7A, 0x46)

    def 页(标题, 副标题=None):
        s = prs.slides.add_slide(空白)
        t = s.shapes.add_textbox(Inches(0.6), Inches(0.28), Inches(12.1), Inches(0.9))
        r = t.text_frame.paragraphs[0].add_run()
        r.text = 标题
        r.font.size = Pt(30)
        r.font.bold = True
        r.font.color.rgb = 黑
        if 副标题:
            t2 = s.shapes.add_textbox(Inches(0.62), Inches(1.12), Inches(12.1), Inches(0.6))
            r2 = t2.text_frame.paragraphs[0].add_run()
            r2.text = 副标题
            r2.font.size = Pt(16)
            r2.font.color.rgb = 深
        return s

    def 字块(s, x, y, w, h, 行s, 号=18, 色=黑, 粗=False):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, 行 in enumerate(行s):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            r = p.add_run()
            r.text = 行
            r.font.size = Pt(号)
            r.font.color.rgb = 色
            r.font.bold = 粗
            p.space_after = Pt(8)
        return tb

    # 1 封面
    s = prs.slides.add_slide(空白)
    字块(s, 0.8, 1.6, 11.8, 1.4, ["阈值是什么？"], 号=44, 色=黑, 粗=True)
    字块(s, 0.8, 3.0, 11.8, 1.0, ["用你自己项目里的游戏画面，一页一页看明白"], 号=22, 色=深)
    字块(s, 0.8, 4.2, 11.8, 1.6,
        ["每一页左边是“电脑看到的红”，右边是你需要记的唯一一句话。",
         "第 7 页会自动播放：阈值从 0 一路抬到 255。"], 号=16, 色=深)

    # 2 一句话
    s = 页("一句话：阈值 = 分数线")
    字块(s, 0.9, 1.9, 11.5, 2.4,
        ["考试分数 = 一个点的“红”有多少（R 值，0～255）",
         "阈值     = 及格线",
         "分数 ≥ 及格线  →  算“红”",
         "分数线抬高      →  及格的点变少"], 号=24, 色=黑)

    # 3 图是数字
    s = 页("一张图，就是六百多万个数字")
    字块(s, 0.9, 1.8, 11.5, 2.2,
        ["1920 × 1080 = 2,073,600 个点",
         "每个点有 3 个数：蓝、绿、红（各 0～255）",
         "合计 6,220,800 个数 —— 这就是一帧游戏画面"], 号=22)

    # 4 三个数
    s = 页("一个点 = 3 个数", "顺序是 蓝、绿、红（BGR），不是 RGB")
    行 = ["暗红 (B=40, G=20, R=180)  ← 这是血条的颜色",
          "品红 (B=180, G=60, R=180)  ← 这是宠物血条的颜色",
          "白色 (B=255, G=255, R=255)  ← 这是名字牌的颜色",
          "灰墙 (B=120, G=120, R=120)  ← 这是背景"]
    字块(s, 0.9, 1.9, 11.5, 3.2, 行, 号=20)
    字块(s, 0.9, 5.3, 11.5, 1.2,
        ["记住这四个例子，第 10 页会用它们解释“为什么删掉一个条件会打自己人”。"], 号=15, 色=深)

    # 5 四个门卫
    s = 页("判断“红”要靠 4 个门卫，缺一不可")
    行 = ["1 号  R > 140      红要够强",
          "2 号  G < 110      绿不能多   → 拦掉白、灰、黄",
          "3 号  B < 110      蓝不能多   → 拦掉品红、紫、蓝",
          "4 号  R > G + 60   红要明显强于绿"]
    字块(s, 0.9, 1.8, 11.5, 2.6, 行, 号=20)
    字块(s, 0.9, 4.9, 11.5, 1.4,
        ["四个都放行，这个点才算“红”。", "所以：拧松一个门卫，不等于闸门全开。"], 号=18, 色=红, 粗=True)

    # 6 动图
    s = 页("看动画：阈值从 0 抬到 255", "白亮点 = 被判为红色的点；暗灰 = 被淘汰的")
    s.shapes.add_picture(gif, Inches(0.55), Inches(1.75), height=Inches(5.2))
    字块(s, 9.4, 2.0, 3.6, 4.0,
        ["线越低 → 留下的点越多", "线越高 → 留下的点越少",
         "抬到 255 → 一个都不剩"], 号=17, 色=深)
    字块(s, 0.55, 7.02, 9.2, 0.4,
        ["注：这张旧截图上有旧工具画的红框，它们也是红色，所以也会被算进去 —— 第 3 课讲怎么把框和真血条区分开。"],
        号=11, 色=深)

    # 7-12 分步
    for t in 阶梯:
        图, n = 着色(arr, t)
        p = os.path.join(出, "阶梯_%03d.png" % t)
        Image.fromarray(图).save(p)
        s = 页("阈值 = %d" % t, "被判为红色的点：%d 个" % n)
        s.shapes.add_picture(p, Inches(0.5), Inches(1.75), height=Inches(4.7))
        if t == 0:
            行 = ["线降到最低", "1 号门卫几乎全放行", "但 2、3、4 号还站着 ——",
                  "白、灰、蓝仍然进不来"]
        elif t == 100:
            行 = ["比项目里的线松", "留下的点更多", "容易把别的红色也当进来"]
        elif t == 140:
            行 = ["这就是项目真正用的阈值", "红条被稳稳挑出来"]
        elif t == 200:
            行 = ["线抬高了", "暗一点的红也被淘汰", "点变少了"]
        elif t == 254:
            行 = ["只剩 R 正好等于 255 的点", "极少数"]
        else:
            行 = ["R 最大就是 255", "要“超过 255”才及格", "不可能 → 0 个"]
        字块(s, 9.3, 2.0, 3.7, 4.6, 行, 号=17, 色=红 if t == 255 else 深, 粗=(t == 255))

    # 13 为什么 255 = 0
    s = 页("为什么 255 就变成 0 个？")
    字块(s, 0.9, 1.9, 11.6, 3.0,
        ["判据写的是  R > 140，是“大于”，不是“大于等于”",
         "把线定到 255，就要求  R > 255",
         "而一个数最大只能是 255 —— 没有人能超过 255",
         "所以：0 个"], 号=22)
    字块(s, 0.9, 5.2, 11.6, 1.3,
        ["> 和 >= 只差一个字符，结果可以差几千个点。以后看任何阈值代码，先确认是哪个。"], 号=16, 色=红)

    # 14 删掉 B<110
    s = 页("为什么不能删掉 B < 110？", "删掉它 = 3 号门卫下班")
    字块(s, 0.9, 1.9, 11.6, 3.2,
        ["品红 (B=180, G=60, R=180) 逐关检查：",
         "1 号 R>140 → 180>140  ✅ 放行",
         "2 号 G<110 →  60<110  ✅ 放行",
         "3 号 B<110 → 180<110  ❌ 拦下   ← 只靠它挡住",
         "4 号 R>G+60 → 180>120 ✅ 放行"], 号=19)
    字块(s, 0.9, 5.3, 11.6, 1.4,
        ["3 号一下班，品红就混进来 → 宠物/英雄的血条被当成怪 → 打自己人。这就是项目里真出过的事故。"],
        号=16, 色=红)

    # 15 收尾
    s = 页("记住这三句就走够了")
    字块(s, 0.9, 1.8, 11.6, 3.4,
        ["① 阈值是分数线：线抬高，合格变少",
         "② 判据是“并且”：拧松一个，闸门不全开",
         "③ 数值 ≠ 数量：255 是分数，不是个数"], 号=24)
    字块(s, 0.9, 5.2, 11.6, 1.4,
        ["这条判据在项目里的位置：眼\\血条.py  →  红条()",
         "想自己试：python 教学\\第2课.py 数红 100"], 号=15, 色=深)

    p = os.path.join(HERE, "阈值演示.pptx")
    prs.save(p)
    print("已生成：", p)
    print("动图：", gif, "%.1f MB" % (os.path.getsize(gif) / 1e6))
    print("各阈值点数：", 数)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
