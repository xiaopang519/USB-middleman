# -*- coding: utf-8 -*-
"""支撑层 · 标定换算

标定 = 测出"1格游戏距离 = 多少像素"（px_per_cell）

为什么需要标定：
  感知层输出客户区像素坐标
  决策层寻路要格子坐标
  换算：格子 = 像素 ÷ px_per_cell

标定方法（走步法）：
  1. 角色站定，记录格子坐标（OCR读 或 看画面显示）
  2. 走N步，记录新格子坐标
  3. 量画面上固定参照物（墙角/NPC）移动了多少像素
  4. px_per_cell = 参照物像素位移 ÷ 格子位移

为什么用参照物不用角色：
  俯视角游戏角色永远在画面中心，走动时角色不动画面动。
  参照物在画面上移动，能量出像素位移。

标定失效条件：分辨率变 / 窗口尺寸变 / UI缩放变 → 重新标定。
"""
import json
import os

CALIB_FILE = "calib_grid.json"


def calibrate(grid_dx, grid_dy, pixel_dx, pixel_dy):
    """走步法标定，算 px_per_cell。

    参数：
      grid_dx, grid_dy: 走的格子位移
      pixel_dx, pixel_dy: 参照物像素位移

    返回：px_per_cell（每格多少像素）
    """
    results = []
    if grid_dx != 0 and pixel_dx != 0:
        results.append(abs(pixel_dx / grid_dx))
    if grid_dy != 0 and pixel_dy != 0:
        results.append(abs(pixel_dy / grid_dy))
    if not results:
        raise ValueError("格子位移和像素位移都不能全为0")
    return sum(results) / len(results)


def save_calib(px_per_cell, origin_grid=None, origin_pixel=None, path=CALIB_FILE):
    """存标定数据到 json。"""
    data = {"px_per_cell": px_per_cell}
    if origin_grid:
        data["origin_grid"] = list(origin_grid)
    if origin_pixel:
        data["origin_pixel"] = list(origin_pixel)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_calib(path=CALIB_FILE):
    """读标定数据。返回 dict 或 None。"""
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def to_grid(px, py, px_per_cell):
    """客户区像素 → 格子坐标"""
    return (int(px // px_per_cell), int(py // px_per_cell))


def to_pixel(gx, gy, px_per_cell):
    """格子坐标 → 客户区像素"""
    return (int(gx * px_per_cell), int(gy * px_per_cell))


if __name__ == "__main__":
    # 标定演示
    ppc = calibrate(5, 0, 240, 0)
    print("走5格移240像素 → px_per_cell = %.1f" % ppc)
    print("换算: 客户区(975,260) → 格子%s" % str(to_grid(975, 260, ppc)))
