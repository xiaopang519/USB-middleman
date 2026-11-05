# 第三方组件与许可

本仓库主体代码为 MIT（见 `LICENSE`）。下面这些不是我们的代码，分发时按各自条款附上说明。

## 1. hid-remapper —— 固件上游（MIT）

- 上游：https://github.com/jfedor2/hid-remapper
- 许可：MIT（上游 LICENSE 原文：*The MIT License applies to the software in this repository*）
- 许可全文副本：`docs/third-party/hid-remapper-LICENSE.txt`
- 本仓库与它的关系：
  - `手/固件/patches/hr-patches.diff` 是对上游的补丁（源码，可重现）
  - `手/固件/uf2/*.uf2` 是"上游 + 该补丁"的编译产物
  - `手/固件/reference/build-chain.md` 是从零构建的完整步骤（pico-sdk 2.1.1 + arm-none-eabi-gcc + ninja）

MIT 允许再分发二进制，前提是保留版权与许可声明 —— 上面两条即是。

## 2. Interception —— **未随仓库分发**

- 上游：https://github.com/oblitum/Interception
- 用途：`SDK/action/mouse_inject.py` 的驱动级注入路径（软件兜底，非本项目主线）
- 许可：双许可 —— **非商业用途 LGPL v3.0；商业用途需向作者购买授权**（francisco@oblita.com）
- 本仓库**不包含** `interception.dll` 与驱动本体。要使用该路径请自行从上游客服端安装，
  并自行确认你的使用场景符合其许可。

## 3. Python 依赖（不随仓库分发，由 pip 安装）

| 包 | 许可 |
|---|---|
| `hid`（hidapi 绑定） | BSD-3-Clause（hidapi）/ MIT（绑定层） |
| `numpy` | BSD-3-Clause |
| `opencv-python` | Apache-2.0（OpenCV）|
| `Pillow` | HPND |
| `python-pptx`（仅教学脚本用） | MIT |

## 4. 字体与图标

本仓库不含任何第三方字体或图标资源。
