#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
素材体检：找出"被抠穿的身体"与"合法的封闭透明缝隙"。

浅色角色（奶油肚皮、白眼睛）被抠穿时，缩略图看上去完全正常，必须放到有内容
的背景上才看得出来 —— 所以要用数据查，不能靠眼看。

成品素材里已经没有 pre（擦阴影之前的 alpha）了，所以这里只能用间接特征：
被抠穿的身体一定是**又大、又亮、又是角色色**的洞；而手臂下方、两腿之间那些
合法缝隙要么在阴影里偏暗（实测均色 (176,147,111)、亮度 178），要么接近纯白
（(254,252,252)、饱和差 1.8）。据此分类，最后再人工抽查报警帧。

真正的因果判定在 process_assets.py 的 interior_hole_split() 里，它拿得到 pre。

用法：
    python tools/scan_holes.py [assets/pet]
退出码非 0 表示存在疑似被抠穿的身体。
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

SAT_MIN = 12.0        # 角色色下限（纯白背景饱和差 ~2）
LUM_MIN = 205.0       # 肚皮这类浅色身体很亮；阴影里的缝隙偏暗
MIN_SUSPECT = 3000    # 小洞不算"抠穿"，是抗锯齿或高光
MIN_AREA = 25         # 忽略抗锯齿级别的零星小洞


def scan(pet_dir: Path):
    files = sorted((pet_dir / "laugh").glob("*.webp"))
    idle = pet_dir / "idle.webp"
    if idle.exists():
        files.append(idle)
    if not files:
        print("没找到素材帧:", pet_dir)
        return 2

    body_total = pocket_total = soft_total = 0
    body_frames = []
    for f in files:
        a = np.array(Image.open(f).convert("RGBA"), dtype=np.uint8)
        rgb = a[..., :3].astype(np.int32)
        sat = rgb.max(2) - rgb.min(2)
        lum = rgb.mean(2)
        soft_total += int(((a[..., 3] > 0) & (a[..., 3] < 250)).sum())
        empty = (a[..., 3] < 40).astype(np.uint8)
        n, lab = cv2.connectedComponents(empty, 8)
        if n <= 1:
            continue
        border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
        border.discard(0)
        b = 0
        for i in range(1, n):
            if i in border:
                continue
            m = lab == i
            ar = int(m.sum())
            if ar < MIN_AREA:
                continue
            if (ar >= MIN_SUSPECT and float(sat[m].mean()) >= SAT_MIN
                    and float(lum[m].mean()) >= LUM_MIN):
                b += ar
            else:
                pocket_total += ar
        if b:
            body_frames.append((b, f.name))
        body_total += b

    body_frames.sort(reverse=True)
    print("扫描帧数            : %d" % len(files))
    print("疑似被抠穿的身体    : %d px，涉及 %d 帧" % (body_total, len(body_frames)))
    print("其他内部透明区(合法): %d px" % pocket_total)
    print("半透明像素总计      : %d（平均 %.0f/帧）"
          % (soft_total, soft_total / len(files)))
    if body_frames:
        print("最严重:", ", ".join("%s=%d" % (n, v) for v, n in body_frames[:8]))
    ok = body_total == 0
    print("判定:", "通过 ✓" if ok else "存在被抠穿的身体 ✗")
    return 0 if ok else 1


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1
                  else Path(__file__).resolve().parent.parent / "assets" / "pet")
    sys.exit(scan(target))
