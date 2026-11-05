# -*- coding: utf-8 -*-
"""感知层 · 色块识别（连通域分析）

从画面里找目标（示例：找红色横条 = 找血条 = 找怪）。

三步流程（所有图色识别的通用套路）：
  1. 阈值(Threshold)：判每个像素算不算目标 → 布尔掩码
  2. 连通域标记(Connected Component Labeling)：相邻目标像素聚成区域
  3. 过滤(Filter)：按面积/形状剔除噪点

换游戏时改这里：
  - R_MIN/G_MAX/B_MAX：目标颜色阈值（采样真实画面定）
  - MAX_BAR_H/MIN_BAR_RATIO：目标形状（横条/方块/圆点）
  - Y_MAX：扫描区域（目标在画面哪部分）
"""
import numpy as np
from PIL import Image
from scipy.ndimage import label, find_objects, center_of_mass

# === 颜色阈值（示例：红色横条。换游戏要采样真实颜色）===
R_MIN, G_MAX, B_MAX = 150, 80, 80

# === 面积过滤 ===
MIN_AREA = 20
MAX_AREA = 2000

# === 形状过滤（示例：细横条。换目标形状要改）===
MAX_BAR_H = 4         # 最大高度（像素）
MIN_BAR_RATIO = 5     # 宽/高 最小比例

# === 扫描区域（示例：只扫上半部分。换游戏改）===
Y_MAX = 400


def build_mask(arr, y_max=Y_MAX):
    """第1步：阈值。返回布尔掩码 True=目标像素。"""
    top = arr[:y_max]
    r, g, b = top[:, :, 0], top[:, :, 1], top[:, :, 2]
    return (r > R_MIN) & (g < G_MAX) & (b < B_MAX)


def find_targets(img_or_arr, y_max=Y_MAX):
    """主接口：传 PIL 图或 numpy 数组，返回目标列表。

    返回: [{"cx": 中心x, "bar_y": y, "bar_w": 宽, "bar_h": 高, "area": 面积}, ...]
          坐标系：客户区像素
    """
    if isinstance(img_or_arr, Image.Image):
        arr = np.array(img_or_arr)
    else:
        arr = img_or_arr

    mask = build_mask(arr, y_max)

    # 第2步：连通域标记（8连通，相邻目标像素聚成区域）
    structure_8conn = [[1,1,1],[1,1,1],[1,1,1]]
    labeled, n = label(mask, structure=structure_8conn)
    if n == 0:
        return []

    # 第3步：过滤（面积 + 形状）
    targets = []
    slices = find_objects(labeled)
    for i, sl in enumerate(slices, 1):
        region = labeled[sl] == i
        area = int(region.sum())
        if area < MIN_AREA or area > MAX_AREA:
            continue
        ys_r, xs_r = np.where(region)
        x1_r, x2_r = xs_r.min(), xs_r.max()
        y1_r, y2_r = ys_r.min(), ys_r.max()
        bar_w = x2_r - x1_r + 1
        bar_h = y2_r - y1_r + 1
        if bar_h > MAX_BAR_H or bar_w < bar_h * MIN_BAR_RATIO:
            continue
        # 加回切片偏移得画面绝对坐标
        y_off, x_off = sl[0].start, sl[1].start
        ys, xs = np.where(region)
        xs = xs + x_off
        ys = ys + y_off
        x1, x2 = xs.min(), xs.max()
        y1, y2 = ys.min(), ys.max()
        cy, cx = center_of_mass(region)
        targets.append({
            "cx": int(cx + x_off),
            "bar_y": int(cy + y_off),
            "bar_w": x2 - x1 + 1,
            "bar_h": y2 - y1 + 1,
            "area": area,
            "x1": int(x1),
            "x2": int(x2),
        })
    return targets


# 兼容旧名（find_monsters = find_targets）
find_monsters = find_targets


if __name__ == "__main__":
    img = Image.open("frame.png")
    targets = find_targets(img)
    print("画面尺寸: %dx%d" % img.size)
    print("找到 %d 个目标：" % len(targets))
    for t in targets:
        print("  中心(%d,%d) 宽%d 高%d 面积%d"
              % (t["cx"], t["bar_y"], t["bar_w"], t["bar_h"], t["area"]))
