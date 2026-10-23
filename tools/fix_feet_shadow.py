#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清除脚底残留的"暖色地面阴影块"。

问题：process_assets.py 的 remove_white_shadow 只清"中性灰"阴影
（饱和差 mx-mn < 35 且亮度 > 150）。但源视频里的地面接触阴影是**暖米色**，
再经过 h264 宏块量化，会变成一整块饱和差 28~60 的浅粉米色方块，
刚好躲过那条判据 —— 结果就是脚底留着一坨斑点。

为什么不能直接放宽全局判据：colorkey 的 blend 会在角色边缘生成一圈
半透明"白色光晕"，那些光晕像素同样是低饱和高亮度。一旦把它们算成
"可通行的阴影"，泛洪就会顺着光晕漏进角色内部，把奶油色肚皮整个吃掉。
实测放宽到饱和差<70 时，清除量从每帧 128 像素暴涨到 3986 像素，
且大部分落在画面上半部 —— 那就是肚皮。

所以这里的做法是**把搜索范围限制在画面底部**（肚皮不在那里），
并且只清除"能从画面外经透明/阴影走到"的像素：

    区域:  y >= 0.85 * H
    颜色:  饱和差 < 70 且 最亮通道 > 150
    连通:  从底边泛洪，可穿过"全透明"或"带内阴影"

刻意保留亮度 <= 150 的深色接触阴影：那是让角色"站在地上"的必要线索，
全清掉会显得飘。

用法:
    python tools/fix_feet_shadow.py assets/pet            # 就地修复
    python tools/fix_feet_shadow.py assets/pet --dry-run  # 只统计不写
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import cv2
from PIL import Image

BAND_TOP = 0.85      # 只处理画面底部 15%
SAT_MAX = 70         # 饱和差上限（原判据 35 太紧）
MX_MIN = 150         # 亮度下限：低于此的是深色接触阴影，保留
QUALITY_LAUGH = 88   # 与 process_assets.py 保持一致
QUALITY_IDLE = 92


def shadow_mask(rgba):
    """返回需要清除的暖色残留阴影掩码"""
    rgb = rgba[..., :3].astype(np.int32)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    h, w = rgba.shape[:2]
    occupied = rgba[..., 3] > 0
    zone = np.zeros((h, w), dtype=bool)
    zone[int(h * BAND_TOP):, :] = True
    is_shadow = occupied & ((mx - mn) < SAT_MAX) & (mx > MX_MIN) & zone
    walk = (~occupied | is_shadow).astype(np.uint8) * 255
    walk = np.vstack([walk, np.full((1, w), 255, dtype=np.uint8)])
    mask = np.zeros((h + 3, w + 2), np.uint8)
    cv2.floodFill(walk, mask, (w // 2, h), 128, loDiff=0, upDiff=0)
    return (walk[:h] == 128) & is_shadow


def fix_image(path, quality, dry_run, tmpdir):
    img = Image.open(path).convert("RGBA")
    rgba = np.asarray(img, dtype=np.uint8).copy()
    before_shape = rgba.shape
    got = shadow_mask(rgba)
    n = int(got.sum())
    if n and not dry_run:
        rgba[got] = 0
        rgba[rgba[..., 3] == 0, :3] = 0          # 透明像素 RGB 清零，避免缩放插值带出杂色
        out = Image.fromarray(rgba, mode="RGBA")
        staged = os.path.join(tmpdir, os.path.basename(str(path)))
        out.save(staged, "WEBP", lossless=False, quality=quality, method=4)
        return n, staged
    return n, None


def main():
    ap = argparse.ArgumentParser(description="清除脚底暖色残留阴影块")
    ap.add_argument("pet_dir", help="assets/pet 目录")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    pet = Path(args.pet_dir)
    laugh = pet / "laugh"
    targets = []
    idle = pet / "idle.webp"
    if idle.exists():
        targets.append((idle, QUALITY_IDLE))
    if laugh.is_dir():
        targets += [(p, QUALITY_LAUGH) for p in sorted(laugh.glob("*.webp"))]
    if not targets:
        sys.exit(f"没找到素材：{pet}")

    print(f"处理 {len(targets)} 张（idle + {len(targets)-1} 帧）"
          f"  判据: y>={BAND_TOP:.2f}H, 饱和差<{SAT_MAX}, 亮度>{MX_MIN}")

    total = 0
    touched = 0
    worst = (0, "")
    with tempfile.TemporaryDirectory(prefix="feetfix_") as tmp:
        staged = []
        for path, q in targets:
            n, out_path = fix_image(path, q, args.dry_run, tmp)
            total += n
            if n:
                touched += 1
            if n > worst[0]:
                worst = (n, path.name)
            if out_path:
                staged.append((out_path, path))
        if args.dry_run:
            print("[dry-run] 未写入任何文件")
        else:
            for src, dst in staged:
                shutil.copyfile(src, dst)
            print(f"已写回 {len(staged)} 张")

    print(f"共清除 {total} 像素，涉及 {touched}/{len(targets)} 张，最大单帧 {worst[0]} ({worst[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
