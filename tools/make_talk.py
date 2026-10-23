#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从待机帧生成"说话"张嘴动画（不需要额外美术素材）。

第一版是"把嘴那条细线等比拉伸"，拉大了就变成一块平顶平底的黑色板子 ——
没有嘴角收尖、内部一片平黑。这一版按真正的嘴来做：

  1. 先量出嘴线的逐列上沿/下沿（它是 3/4 视角，本来就带一点斜度）；
  2. 开口用**透镜形**：高度 h(x) = H * sqrt(1 - ((x-cx)/halfW)^2)，
     两端自然收尖到 0，所以嘴角是尖的、不会出现直角；
  3. 下颌按同一个位移场下沉，脸颊平滑收边，轮廓连续；
  4. 开口内部按"上深下暖"的竖直渐变重画，再加边缘压暗，避免死平；
  5. 上唇压一条暗边、下唇压一条暖亮边，把嘴唇的形交代出来；
  6. 张得够大时给一点舌头的暖色。

输出 assets/pet/talk/NN.webp，与 idle 同画布同坐标系。

用法:
    python tools/make_talk.py                    # 默认 6 档
    python tools/make_talk.py --max-open 22 --preview p.png
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PET = ROOT / "assets" / "pet"

# 口腔配色（取自大笑帧实测的口腔均色 (45,20,9)，再往上/下各做一点变化）
DARK_TOP = np.array([26, 11, 5], dtype=np.float32)      # 上颚阴影
DARK_MID = np.array([52, 22, 10], dtype=np.float32)     # 口腔中段
WARM_BOT = np.array([122, 58, 42], dtype=np.float32)    # 靠下偏暖（舌/喉）
LIP_EDGE = np.array([58, 30, 12], dtype=np.float32)     # 上唇暗边
LIP_LIGHT = np.array([206, 150, 92], dtype=np.float32)  # 下唇亮边

TAIL_X = 0.22        # 位移场横向收边（相对半宽的比例）
JAW_UNDER = 26       # 下颌区：位移保持满值的下沿（相对嘴底）
NECK_FADE = 64       # 到脖子的收边长度


def find_mouth(rgba):
    g = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)
    h, w = g.shape
    band = np.zeros((h, w), bool)
    band[:int(h * 0.30), :] = True
    dark = (g < 105) & (rgba[..., 3] > 200) & band
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), 8)
    best = None
    for i in range(1, n):
        x, y, bw, bh, ar = st[i]
        if ar < 30 or bh == 0 or bw / bh < 3.0:
            continue
        if best is None or ar > best[4]:
            best = (x, y, bw, bh, ar)
    if best is None:
        raise SystemExit("没找到嘴（又宽又扁的深色块），检查待机帧")
    x, y, bw, bh, ar = best
    return (int(x), int(x + bw), int(y), int(y + bh), float(ar))


def mouth_line(base, mouth):
    """逐列量出嘴线的上沿与下沿（NaN 处用邻列插值）。"""
    g = cv2.cvtColor(base[..., :3], cv2.COLOR_RGB2GRAY)
    a = base[..., 3]
    h, w = g.shape
    mx0, mx1, my0, my1, _ = mouth
    top = np.full(w, np.nan, dtype=np.float32)
    bot = np.full(w, np.nan, dtype=np.float32)
    for x in range(max(0, mx0 - 3), min(w, mx1 + 4)):
        y0, y1 = max(0, my0 - 8), min(h, my1 + 10)
        col = g[y0:y1, x]
        idx = np.where((col < 108) & (a[y0:y1, x] > 200))[0]
        if len(idx):
            top[x] = y0 + idx.min()
            bot[x] = y0 + idx.max()
    for arr in (top, bot):
        good = ~np.isnan(arr)
        if good.sum() < 3:
            raise SystemExit("嘴线太短，量不出上下沿")
        xs = np.arange(w)
        arr[~good] = np.interp(xs[~good], xs[good], arr[good])
    # 横向轻微平滑，避免逐像素抖动
    k = np.hanning(9); k /= k.sum()
    top = np.convolve(top, k, mode="same")
    bot = np.convolve(bot, k, mode="same")
    return top, bot


def displacement(h, w, mouth, amount):
    """下颌下沉的位移场 dy(x,y)，以及开口的横向形状 fx(x)。"""
    mx0, mx1, my0, my1, _ = mouth
    cx = 0.5 * (mx0 + mx1)
    half = max(3.0, 0.5 * (mx1 - mx0))

    xs = np.arange(w, dtype=np.float32)
    ys = np.arange(h, dtype=np.float32)

    # 横向：严格的椭圆 —— 这正是"嘴角收尖"的来源
    t = np.clip((xs - cx) / (half * (1.0 + TAIL_X)), -1.0, 1.0)
    fx = np.sqrt(np.maximum(0.0, 1.0 - t * t))

    fy = np.zeros(h, dtype=np.float32)
    lip_up = max(1, my0 - 1)
    fy[lip_up:my1] = np.linspace(0.0, 1.0, max(1, my1 - lip_up))
    jaw_bot = min(h, my1 + JAW_UNDER)
    fy[my1:jaw_bot] = 1.0
    neck = min(h, jaw_bot + NECK_FADE)
    if neck > jaw_bot:
        fy[jaw_bot:neck] = np.linspace(1.0, 0.0, neck - jaw_bot)

    dy = amount * np.outer(fy, fx).astype(np.float32)
    return dy, fx


def make_frame(base, mouth, top, amount):
    h, w = base.shape[:2]
    dy, fx = displacement(h, w, mouth, amount)

    map_x = np.tile(np.arange(w, dtype=np.float32), (h, 1))
    map_y = np.arange(h, dtype=np.float32)[:, None] - dy
    rgb = cv2.remap(base[..., :3], map_x, map_y, cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE)
    alpha = cv2.remap(base[..., 3], map_x, map_y, cv2.INTER_LINEAR,
                      borderMode=cv2.BORDER_REPLICATE)
    rgb = np.nan_to_num(rgb, nan=0.0)
    alpha = np.nan_to_num(alpha, nan=0.0)
    out = np.dstack([np.clip(rgb, 0, 255), np.clip(alpha, 0, 255)]).astype(np.uint8)
    out[..., 3] = cv2.morphologyEx(out[..., 3], cv2.MORPH_CLOSE,
                                   np.ones((3, 3), np.uint8))

    if amount < 0.5:
        return out

    # ---- 画开口：透镜形，中间高、两端收尖 ----
    yy = np.arange(h, dtype=np.float32)[:, None]
    xx = np.arange(w, dtype=np.float32)[None, :]
    up = top[None, :]                     # 上唇线
    opening = dy - 1.0                    # 开口高度（每列）
    inside = (yy >= up) & (yy < up + np.maximum(opening, 0.0))
    if not inside.any():
        return out

    u = np.clip((yy - up) / np.maximum(opening, 1e-3), 0.0, 1.0)   # 0=上沿 1=下沿
    # 竖直渐变：上暗 -> 中段口腔 -> 下暖
    col = (DARK_TOP[None, None, :] * (1 - u[..., None]) ** 2
           + DARK_MID[None, None, :] * (2 * u[..., None] * (1 - u[..., None]))
           + WARM_BOT[None, None, :] * (u[..., None] ** 2))
    # 边缘压暗：靠近嘴角/上下唇的地方再收一点，避免死平
    edge = np.clip(1.0 - np.abs(2 * u - 1) ** 3, 0, 1)[..., None]
    lat = np.clip(fx[None, :, None] * 1.35, 0, 1)                   # 嘴角处更暗
    mix = inside[..., None].astype(np.float32)
    paint = col * (0.55 + 0.45 * edge) * (0.72 + 0.28 * lat)
    out[..., :3] = np.where(mix > 0,
                            out[..., :3].astype(np.float32) * (1 - mix) + paint * mix,
                            out[..., :3]).astype(np.uint8)

    # 舌头：张得够大时给一点暖色，位置在开口下部中间
    if amount > 8:
        tx = 0.5 * (mouth[0] + mouth[1])
        half = 0.5 * (mouth[1] - mouth[0])
        ty = top[mouth[0]:mouth[1]].mean() + amount * 0.66
        axes = (max(3, int(half * 0.55)), max(2, int(amount * 0.22)))
        layer = np.zeros((h, w), np.uint8)
        cv2.ellipse(layer, (int(tx), int(ty)), axes, 0, 0, 360, 255, -1,
                    cv2.LINE_AA)
        soft = cv2.GaussianBlur(layer.astype(np.float32) / 255.0, (0, 0), 1.2)
        soft = (soft * inside.astype(np.float32) * 0.55)[..., None]
        tongue = np.array([150, 88, 78], dtype=np.float32)[None, None, :]
        out[..., :3] = (out[..., :3].astype(np.float32) * (1 - soft)
                        + tongue * soft).astype(np.uint8)

    # ---- 唇线 ----
    # 上唇：沿开口上沿压一条暗边
    pts_up = [(int(x), int(round(top[x])))
              for x in range(mouth[0], mouth[1] + 1) if dy[0, x] > 0.8]
    if len(pts_up) >= 2:
        cv2.polylines(out, [np.array(pts_up, np.int32)], False,
                      tuple(int(v) for v in LIP_EDGE) + (255,), 2, cv2.LINE_AA)
    # 下唇：沿开口下沿压一条亮边（比上唇细一点）
    pts_lo = [(int(x), int(round(top[x] + dy[0, x] - 1)))
              for x in range(mouth[0], mouth[1] + 1) if dy[0, x] > 1.5]
    if len(pts_lo) >= 2:
        cv2.polylines(out, [np.array(pts_lo, np.int32)], False,
                      tuple(int(v) for v in LIP_LIGHT) + (255,), 1, cv2.LINE_AA)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="0.18,0.34,0.50,0.66,0.82,1.0")
    ap.add_argument("--max-open", type=float, default=22.0,
                    help="最大张嘴量（像素，560 画布下）。透镜形开口，"
                         "18~24 自然，再大下唇会被拉长")
    ap.add_argument("--idle", default=str(PET / "idle.webp"))
    ap.add_argument("--out", default=str(PET / "talk"))
    ap.add_argument("--preview", default=None)
    args = ap.parse_args()

    base = np.array(Image.open(args.idle).convert("RGBA"), dtype=np.uint8)
    mouth = find_mouth(base)
    top, bot = mouth_line(base, mouth)
    cx = (mouth[0] + mouth[1]) // 2
    print("嘴位置: x %d-%d, y %d-%d, 上沿在中心处 %.1f, 厚 %.1f"
          % (mouth[0], mouth[1], mouth[2], mouth[3], top[cx], bot[cx] - top[cx]))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.webp"):
        old.unlink()

    steps = [float(v) for v in args.steps.split(",")]
    frames = []
    for i, s in enumerate(steps):
        f = make_frame(base, mouth, top, args.max_open * s)
        p = out / f"{i:02d}.webp"
        Image.fromarray(f, "RGBA").save(p, "WEBP", lossless=False, quality=92,
                                        method=4, exact=True)
        frames.append(p)
    print("生成 %d 帧 -> %s" % (len(frames), out))

    # 自检：开口随档位单调增；没有被身体包住的空洞
    print("\n自检：")

    def mouth_dark(a):
        mx0, mx1, my0, my1, _ = mouth
        sub = a[my0 - 4:my1 + int(args.max_open) + 8, mx0 - 6:mx1 + 6]
        g = cv2.cvtColor(sub[..., :3], cv2.COLOR_RGB2GRAY)
        return int(((g < 95) & (sub[..., 3] > 200)).sum())

    def enclosed_holes(a):
        empty = (a[..., 3] < 40).astype(np.uint8)
        n, lab = cv2.connectedComponents(empty, 8)
        if n <= 1:
            return 0
        border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
        border.discard(0)
        return int(np.isin(lab, list(set(range(1, n)) - border)).sum())

    prev = -1
    for p, s in zip(frames, steps):
        a = np.array(Image.open(p).convert("RGBA"), dtype=np.uint8)
        d = mouth_dark(a)
        holes = enclosed_holes(a)
        print("  %s  t=%.2f  嘴部深色 %5d  体内空洞 %d  %s"
              % (p.name, s, d, holes, "OK" if d > prev else "!! 没有变大"))
        prev = d
        if holes:
            raise SystemExit("张嘴帧出现被包住的空洞")
    print("画布:", base.shape[1], "x", base.shape[0], "(与 idle 一致)")

    if args.preview:
        tiles = [base] + [np.array(Image.open(p).convert("RGBA"), dtype=np.uint8)
                          for p in frames]
        crops = []
        for im in tiles:
            mx0, mx1, my0, my1, _ = mouth
            x0, x1 = max(0, mx0 - 80), min(im.shape[1], mx1 + 160)
            y0, y1 = max(0, my0 - 95), min(im.shape[0], my1 + 130)
            c = im[y0:y1, x0:x1]
            al = c[..., 3:].astype(np.float32) / 255.0
            crops.append((c[..., :3] * al + 128 * (1 - al)).astype(np.uint8))
        sc = 4
        crops = [cv2.resize(c, None, fx=sc, fy=sc, interpolation=cv2.INTER_LANCZOS4)
                 for c in crops]
        H = max(c.shape[0] for c in crops)
        padded = [np.vstack([c, np.full((H - c.shape[0], c.shape[1], 3), 128, np.uint8)])
                  for c in crops]
        row = np.hstack([np.hstack([p, np.full((H, 6, 3), 255, np.uint8)]) for p in padded])
        # 用 PIL 而不是 cv2.imwrite：后者遇到含中文的路径会**静默失败**（返回 False 不报错）
        Image.fromarray(row).save(args.preview)
        print("预览:", args.preview)
    print(json.dumps({"mouth": mouth[:4], "steps": steps,
                      "max_open": args.max_open}, ensure_ascii=False))


if __name__ == "__main__":
    main()
