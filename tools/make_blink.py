#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从待机帧生成眨眼序列（不需要额外素材）。

做法：先自动定位眼睛（虹膜色块 + 瞳孔暗块），再用"眼睑下拉"合成不同闭合度的帧：
  1) 被眼睑盖住的像素，用该列眼睛上缘外侧 2px 的肤色填充 —— 头的明暗是横向渐变的，
     逐列取色能保住原有光影，比整块填一个纯色自然得多；
  2) 眼睑边缘画一条向下微弧的线，颜色取角色自身描边色，末端略细；
  3) 远处那只眼本来就是一条窄缝，同样处理，只是幅度小。

输出 assets/pet/blink/00..NN.webp，与 idle.webp 同一画布尺寸同一坐标系，
运行时直接替换当前帧，位置不会跳。

用法：
    python tools/make_blink.py                # 生成默认 5 档闭合度
    python tools/make_blink.py --steps 0.2,0.45,0.7,0.9,1.0
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PET = ROOT / "assets" / "pet"

# 眼睑几何：填充与自检共用同一套常量，避免改一处漏一处
PAD = 1.22          # 拟合椭圆停在描边内侧，外扩一点才能整个盖住
LID_START = 0.06    # t=0 时眼皮已经压住本列高度的比例
LID_TRAVEL = 0.94   # 到 t=1 走完剩余高度：保证闭眼时不露虹膜
LINE_SPAN = 0.80    # 眼睑线只画中间这部分，两端收尖


def cover_y(cx, cy, rx, ry, dx, t):
    """逐列水位线 y：这列里 y 小于它的部分被眼皮盖住。

    必须按"该列自己的上下缘"推进，不能用统一高度：眼球是椭圆，两侧列本来就矮，
    统一高度会让两端露出虹膜，或者把线钳到近垂直的极端处画出两根上翘的角。
    """
    dx = np.clip(np.asarray(dx, dtype=np.float64), -1.0, 1.0)
    half = ry * np.sqrt(np.maximum(0.0, 1 - dx * dx))
    return (cy - half) + 2 * half * (LID_START + LID_TRAVEL * t)


def find_eyes(rgba):
    """返回每只眼的椭圆参数：中心、半轴、肤色基准。

    只在头部带（画面顶部 20%）里找：眼睛必然在那里，手、脚上的深色块会被排除。
    """
    rgb = rgba[..., :3].astype(np.int16)
    a = rgba[..., 3]
    h, w = a.shape
    L = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2GRAY)

    band = np.zeros((h, w), bool)
    band[:int(h * 0.20), :] = True
    body = (a > 200) & band

    # 虹膜：绿于红也绿于蓝
    iris = body & (rgb[..., 1] > rgb[..., 0] + 6) & (rgb[..., 1] > rgb[..., 2] + 6)
    n, lab, st, _ = cv2.connectedComponentsWithStats(iris.astype(np.uint8), 8)
    eyes = []
    for i in range(1, n):
        x, y, bw, bh, area = st[i][:5]
        if area >= max(20, (w * h) // 12000):
            eyes.append({"kind": "iris", "bbox": [int(x), int(y), int(bw), int(bh)],
                         "area": int(area)})

    # 深色团块：瞳孔，或侧后方那只只露出一条缝的眼
    dark = body & (L < 95)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8), 8)
    for i in range(1, n2):
        x, y, bw, bh, area = st2[i][:5]
        if area < max(10, (w * h) // 40000):
            continue
        if bh > 0 and bw / bh > 5:          # 那是嘴缝，不是眼睛
            continue
        cx, cy = x + bw / 2.0, y + bh / 2.0
        dup = False
        for e in eyes:
            ex, ey, ew, eh = e["bbox"]
            erx, ery = max(ew / 2, 1) * 1.7, max(eh / 2, 1) * 1.7
            if ((cx - (ex + ew / 2)) / erx) ** 2 + ((cy - (ey + eh / 2)) / ery) ** 2 <= 1:
                dup = True                   # 同一只眼里的瞳孔，不重复算
                break
        if dup:
            continue
        eyes.append({"kind": "pupil", "bbox": [int(x), int(y), int(bw), int(bh)],
                     "area": int(area)})

    # 把每只眼扩成整个眼球：从中心向外走到与肤色接近为止
    out = []
    for e in eyes:
        x, y, bw, bh = e["bbox"]
        cx, cy = x + bw / 2.0, y + bh / 2.0
        # 肤色基准：取眼球外侧 1.7~2.2 倍半轴处，必须跟着眼球尺寸走，
        # 写死像素在高清画布上会落到眼球里面（把虹膜绿当成肤色）。
        probe = []
        hw, hh = max(bw / 2.0, 1.0), max(bh / 2.0, 1.0)
        for mul in (1.7, 2.2, 2.7):
            for sx, sy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                px = int(round(cx + sx * hw * mul))
                py = int(round(cy + sy * hh * mul))
                if 0 <= px < w and 0 <= py < h and a[py, px] > 240:
                    probe.append(rgb[py, px])
        if not probe:
            continue
        skin = np.median(np.array(probe), axis=0)
        rx = ry = 0.0
        for ang in np.linspace(0, 2 * np.pi, 36, endpoint=False):
            r = 1.0
            step = 0.6 if w < 400 else 1.2
            while r < bw * 3.5:
                px = cx + np.cos(ang) * (bw / 2 + r)
                py = cy + np.sin(ang) * (bh / 2 + r)
                ix, iy = int(round(px)), int(round(py))
                if not (0 <= ix < w and 0 <= iy < h):
                    break
                d = np.abs(rgb[iy, ix] - skin).max()
                if d > 42:                      # 还在眼球/描边里
                    r += step
                    continue
                if ang == 0:
                    rx = bw / 2 + r
                if abs(ang - np.pi / 2) < 0.1:
                    ry = bh / 2 + r
                break
            else:
                r = bw * 3.5
        if rx <= 0 or ry <= 0:
            # 用 45° 方向兜底
            rx = max(bw * 0.9, 3)
            ry = max(bh * 0.9, 3)
        out.append({
            "center": [round(cx, 1), round(cy, 1)],
            "radius": [round(max(rx, bw / 2 + 1), 1), round(max(ry, bh / 2 + 1), 1)],
            "kind": e["kind"],
            "skin": [int(v) for v in skin],
        })
    return out


def make_frame(base, eyes, t, outline_rgb):
    """t = 眼睑闭合度 0~1

    填充用"同一行左右两侧肤色的线性内插"，不用 cv2.inpaint：inpaint 会从整圈
    邻域取平均，把额头的高光拉进眼位，结果是一块偏亮的圆盘，边界看得见。
    同一行内插则纵向明暗天然一致（本来就是同一行），横向也接得上渐变，
    而且在眼球边缘处与周围皮肤严格连续，不会露出补丁边。

    眼睑线形状参照大笑序列里真实眯眼的帧（0084 / 0492）：接近直线、略微上凸，
    眼珠内容留在线的下方 —— 不是 ∪ 形弧线，也不是把整只眼糊掉。
    """
    img = base.copy()
    h, w = img.shape[:2]
    src = base[..., :3].astype(np.float32)

    def eye_axes(eye):
        cx, cy = eye["center"]
        return cx, cy, eye["radius"][0] * PAD, eye["radius"][1] * PAD

    L = cv2.cvtColor(base[..., :3], cv2.COLOR_RGB2GRAY)

    def is_skin(y, x):
        if not (0 <= x < w and 0 <= y < h):
            return False
        if base[y, x, 3] < 240:
            return False
        r, g, b = int(base[y, x, 0]), int(base[y, x, 1]), int(base[y, x, 2])
        if g > r + 6 and g > b + 6:          # 虹膜绿
            return False
        return 120 < L[y, x] < 252           # 排除深色描边/瞳孔与接近纯白的高光

    def ref_cols(eye, y_lo, y_hi):
        """逐行往左右找到真正的肤色参考列。

        不能写死列号：远处那只眼紧贴脑袋轮廓，固定外扩 4px 会撞上头部自身的
        深色描边，于是把黑色插进填充区，眼周反而被涂黑（实测 92 个像素）。
        """
        cx = eye["center"][0]
        rx = eye["radius"][0] * PAD
        lc = np.empty(y_hi - y_lo, np.int32)
        rc = np.empty(y_hi - y_lo, np.int32)
        for j, y in enumerate(range(y_lo, y_hi)):
            x = int(round(cx - rx)) - 2
            while x > 0 and (int(round(cx - rx)) - x) < 26 and not is_skin(y, x):
                x -= 1
            lc[j] = x
            x = int(round(cx + rx)) + 2
            while x < w - 1 and (x - int(round(cx + rx))) < 26 and not is_skin(y, x):
                x += 1
            rc[j] = x
        for j, y in enumerate(range(y_lo, y_hi)):
            if not is_skin(y, lc[j]):
                lc[j] = rc[j]
            if not is_skin(y, rc[j]):
                rc[j] = lc[j]
        return lc, rc

    for eye in eyes:
        cx, cy, rx, ry = eye_axes(eye)
        x0 = max(0, int(np.floor(cx - rx)) - 3)
        x1 = min(w, int(np.ceil(cx + rx)) + 4)
        y0 = max(0, int(np.floor(cy - ry)) - 3)
        y1 = min(h, int(np.ceil(cy + ry)) + 4)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        # cover_y 要的是归一化 dx（-1..1），不是像素偏移：直接传偏移会被
        # clip 成 ±1，于是除中心列外每列的 half 都变成 0，只剩上半被盖住
        line_y = cover_y(cx, cy, rx, ry,
                         (np.arange(x0, x1) - cx) / max(rx, 1e-6), t)[None, :]
        inside = (((xx - cx) / max(rx, 1e-6)) ** 2
                  + ((yy - cy) / max(ry, 1e-6)) ** 2) <= 1.0
        covered = inside & (yy < line_y)
        # 先外扩几像素再羽化：软边若正好压在眼球自身那圈深色描边上，
        # 混合系数只有 0.5 左右，会留下一道暗弧
        cov = cv2.dilate(covered.astype(np.uint8),
                         np.ones((5, 5), np.uint8)) > 0
        m = cv2.GaussianBlur(cov.astype(np.float32), (0, 0), 2.2)[..., None]
        lc, rc = ref_cols(eye, y0, y1)
        rows = np.arange(y0, y1)
        u = np.clip((xx - (cx - rx)) / max(1e-6, 2 * rx), 0.0, 1.0)[..., None]
        interp = ((1 - u) * src[rows, lc, :][:, None, :]
                  + u * src[rows, rc, :][:, None, :])
        reg = img[y0:y1, x0:x1, :3].astype(np.float32)
        img[y0:y1, x0:x1, :3] = np.clip(reg * (1 - m) + interp * m, 0, 255)

    if t > 0.05:
        for eye in eyes:
            cx, cy, rx, ry = eye_axes(eye)
            pts = []
            for i in range(41):
                dx = -LINE_SPAN + 2 * LINE_SPAN * i / 40.0
                pts.append((int(round(cx + dx * rx)),
                            int(round(cover_y(cx, cy, rx, ry, dx, t)))))
            for i in range(len(pts) - 1):
                dxm = abs(-LINE_SPAN + 2 * LINE_SPAN * (i + 0.5) / 40.0)
                taper = max(0.0, 1.0 - (dxm / LINE_SPAN) ** 2) ** 0.6
                # 粗细跟着闭合度走：半闭时线压在眼球自身的深色描边上，
                # 再画粗就会糊成一根"眉毛"（真实眯眼帧里的线是细的）
                thick = int(round(ry * 0.085 * (0.25 + 0.75 * taper) * (0.55 + 0.45 * t)))
                if thick < 1:
                    continue
                # 必须给满 4 个分量：img 是 RGBA，传 3 元组会让 OpenCV 把 alpha 填成 0，
                # 等于在眼皮上切出一条能看穿桌面的缝（实测每张帧挖出约 180 个洞像素）
                cv2.line(img, pts[i], pts[i + 1], outline_rgb + (255,), thick,
                         cv2.LINE_AA)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", default="0.22,0.45,0.7,0.9,1.0")
    ap.add_argument("--idle", default=str(PET / "idle.webp"))
    ap.add_argument("--out", default=str(PET / "blink"))
    ap.add_argument("--preview", default=None, help="输出一张预览拼图")
    args = ap.parse_args()

    base = np.array(Image.open(args.idle).convert("RGBA"), dtype=np.uint8)
    eyes = find_eyes(base)
    print(json.dumps({"idle_size": [int(base.shape[1]), int(base.shape[0])],
                      "eyes": eyes}, indent=2, ensure_ascii=False))
    if not eyes:
        raise SystemExit("没定位到眼睛，检查待机帧")

    L = cv2.cvtColor(base[..., :3], cv2.COLOR_RGB2GRAY)
    dk = (L < 110) & (base[..., 3] > 200)
    ob = base[..., :3][dk].reshape(-1, 3)
    med = np.median(ob, axis=0).astype(int) if len(ob) else np.array([60, 40, 20])
    outline_rgb = (int(med[0]), int(med[1]), int(med[2]))
    print("描边色 RGB =", [int(v) for v in med])

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.webp"):
        old.unlink()
    steps = [float(v) for v in args.steps.split(",")]
    frames = []
    for i, t in enumerate(steps):
        f = make_frame(base, eyes, t, outline_rgb)
        p = out / f"{i:02d}.webp"
        Image.fromarray(f, "RGBA").save(p, "WEBP", lossless=False,
                                        quality=92, method=4, exact=True)
        frames.append(str(p))
    print("生成", len(frames), "帧 ->", out)

    # ---- 自检：眼睑线上方不该有深色像素，下方也不该露出瞳孔 ----
    def eye_dark(a, t=None):
        """返回 (眼皮区域内深色像素, 线以下漏出的深色像素)。

        t=None 时统计整只眼作为全睁基准。逐列按水位线判定，并且在线两侧各留
        3px 不计——那里是我自己画的眼睑线，算进去会把自己吓一跳。
        """
        g = cv2.cvtColor(a[..., :3], cv2.COLOR_RGB2GRAY)
        lid_px = leak = 0
        for eye in eyes:
            cx, cy = eye["center"]
            rx, ry = eye["radius"][0] * PAD, eye["radius"][1] * PAD
            for x in range(max(0, int(cx - rx)), min(a.shape[1], int(cx + rx) + 1)):
                dx = (x - cx) / max(rx, 1e-6)
                if abs(dx) > 1.0:
                    continue
                half = ry * np.sqrt(max(0.0, 1 - dx * dx))
                lo = max(0, int(cy - half))
                hi = min(a.shape[0], int(cy + half))
                if hi <= lo:
                    continue
                col = g[lo:hi, x]
                d = (col < 95) & (a[lo:hi, x, 3] > 200)
                if t is None:
                    lid_px += int(d.sum())
                    continue
                cut = int(cover_y(cx, cy, rx, ry, dx, t)) - lo
                lid_px += int(d[:max(0, cut - 3)].sum())
                leak += int(d[min(len(d), cut + 3):].sum())
        return lid_px, leak

    idle_dark = eye_dark(base, None)[0]
    holes_total = 0
    print("自检（眼睑线以上深色像素，全睁=%d）:" % idle_dark)
    for i, (t, p) in enumerate(zip(steps, frames)):
        a = np.array(Image.open(p).convert("RGBA"), dtype=np.uint8)
        up, dn = eye_dark(a, t)
        holes = int(((a[..., 3] == 0) & (base[..., 3] > 0)).sum())
        holes_total += holes
        print("  %02d t=%.2f  眼皮区深色=%4d (%2.0f%% of open)  线以下漏出=%4d  alpha holes=%d"
              % (i, t, up, 100 * up / max(1, idle_dark), dn, holes))
    if holes_total:
        raise SystemExit("眨眼帧挖出了 %d 个透明像素，alpha 被画丢了" % holes_total)
    last = np.array(Image.open(frames[-1]).convert("RGBA"), dtype=np.uint8)
    up, dn = eye_dark(last, steps[-1])
    if up > idle_dark * 0.15:
        print("警告：最后一帧眼皮区域仍有 %.0f%% 深色像素，眼皮没完全盖住"
              % (100 * up / max(1, idle_dark)))
    if dn > idle_dark * 0.15:
        print("警告：最后一帧眼睑线以下漏出 %.0f%% 深色像素，虹膜没被盖住"
              % (100 * dn / max(1, idle_dark)))

    if args.preview:
        tiles = [base] + [make_frame(base, eyes, t, outline_rgb) for t in steps]
        crop = []
        for im in tiles:
            pad_x = int(im.shape[1] * 0.50)
            pad_y = int(im.shape[1] * 0.43)
            x0 = max(0, int(min(e["center"][0] for e in eyes) - pad_x))
            x1 = min(im.shape[1], int(max(e["center"][0] + e["radius"][0] for e in eyes) + pad_x))
            y1 = min(im.shape[0], int(max(e["center"][1] + e["radius"][1] for e in eyes) + pad_y))
            crop.append(im[0:y1, x0:x1, :3])
        sc = 700 / crop[0].shape[1]
        crop = [cv2.resize(c, None, fx=sc, fy=sc, interpolation=cv2.INTER_LANCZOS4) for c in crop]
        H = max(c.shape[0] for c in crop)
        row = np.hstack([np.pad(c, ((0, H - c.shape[0]), (0, 0), (0, 0))) for c in crop])
        # 用 PIL 而不是 cv2.imwrite：后者遇到含中文的路径会**静默失败**（返回 False 不报错）
        Image.fromarray(row).save(args.preview)
        print("预览:", args.preview)


if __name__ == "__main__":
    main()
