#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成设置页图标（纯标准库，无 Pillow 依赖）。

图形：夜色渐变圆角方块 + 白色挂锁 —— 语义对应「锁屏」。
输出 icon@2x.png(58x87 的一半) 与 icon@3x.png，规格与 Preferences 图标惯例一致：
29pt @2x = 58px，29pt @3x = 87px。

用法：
    python tools/make_icon.py [输出目录]
默认输出到 NoLockWallpaperPrefs/Resources/。
"""

import math
import os
import struct
import sys
import zlib

SS = 4          # 每像素 4x4 超采样，用于抗锯齿
PX = 87 / 3.0   # 归一化坐标下的「一个 @3x 像素」


def _rounded_rect(x, y, x0, y0, x1, y1, r):
    """点是否落在圆角矩形内（归一化坐标）。"""
    if x < x0 or x > x1 or y < y0 or y > y1:
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx = x - cx
    dy = y - cy
    return dx * dx + dy * dy <= r * r


def _ring_upper(x, y, cx, cy, r_out, r_in, y_max):
    """上半圆环（锁梁）。y_max 以下整段封闭，形成 U 形。"""
    dx = x - cx
    dy = y - cy
    d2 = dx * dx + dy * dy
    if d2 > r_out * r_out or d2 < r_in * r_in:
        return False
    # 只保留上半部；两侧竖段向下延伸
    if y <= cy:
        return True
    return abs(dx) >= r_in - 1e-9 and y <= y_max


def _sample(x, y):
    """返回该归一化坐标处的 RGBA（0..1 浮点）。"""
    # --- 背景：圆角方块 + 竖向渐变（深夜蓝 -> 深靛） ---
    if not _rounded_rect(x, y, 0.02, 0.02, 0.98, 0.98, 0.215):
        return (0.0, 0.0, 0.0, 0.0)

    t = (y - 0.02) / 0.96
    # #3F5170 -> #232C40
    bg = (
        (0x3F + (0x23 - 0x3F) * t) / 255.0,
        (0x51 + (0x2C - 0x51) * t) / 255.0,
        (0x70 + (0x40 - 0x70) * t) / 255.0,
    )

    white = (1.0, 1.0, 1.0)
    hole = bg  # 锁孔用背景色，等同于「挖空」

    # --- 锁梁 ---
    if _ring_upper(x, y, 0.5, 0.435, 0.190, 0.118, 0.505):
        return white + (1.0,)

    # --- 锁身 ---
    if _rounded_rect(x, y, 0.245, 0.455, 0.755, 0.805, 0.095):
        # 锁孔：上圆 + 下梯形竖槽
        dx = x - 0.5
        dy = y - 0.600
        if dx * dx + dy * dy <= 0.0555 * 0.0555:
            return hole + (1.0,)
        if 0.615 <= y <= 0.715 and abs(dx) <= 0.0555 * (1.0 - (y - 0.615) / 0.115):
            return hole + (1.0,)
        return white + (1.0,)

    return bg + (1.0,)


def render(size):
    """渲染 size x size 的 RGBA 字节串（含超采样抗锯齿）。"""
    rows = []
    inv = 1.0 / size
    n = SS * SS
    for py in range(size):
        row = bytearray()
        row.append(0)  # filter type 0
        for px in range(size):
            r = g = b = a = 0.0
            for sy in range(SS):
                y = (py + (sy + 0.5) / SS) * inv
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) * inv
                    cr, cg, cb, ca = _sample(x, y)
                    r += cr * ca
                    g += cg * ca
                    b += cb * ca
                    a += ca
            if a > 1e-9:
                # 直通 alpha 混合：先累加预乘，再还原颜色
                row += bytes((
                    max(0, min(255, int(round(r / a * 255)))),
                    max(0, min(255, int(round(g / a * 255)))),
                    max(0, min(255, int(round(b / a * 255)))),
                    max(0, min(255, int(round(a / n * 255)))),
                ))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return b"".join(rows)


def png_bytes(size):
    raw = render(size)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    default_out = os.path.join(os.path.dirname(here), "NoLockWallpaperPrefs", "Resources")
    out = sys.argv[1] if len(sys.argv) > 1 else default_out
    os.makedirs(out, exist_ok=True)

    for scale, name in ((2, "icon@2x.png"), (3, "icon@3x.png")):
        size = 29 * scale
        data = png_bytes(size)
        path = os.path.join(out, name)
        # 二进制写入，避免 Windows 文本模式把 \n 转 CRLF 破坏 PNG
        with open(path, "wb") as f:
            f.write(data)
        print("%-14s %dx%d  %d B" % (name, size, size, len(data)))

    # 1x 也放一份，部分系统版本会回退查找
    data = png_bytes(29)
    path = os.path.join(out, "icon.png")
    with open(path, "wb") as f:
        f.write(data)
    print("%-14s %dx%d  %d B" % ("icon.png", 29, 29, len(data)))


if __name__ == "__main__":
    main()