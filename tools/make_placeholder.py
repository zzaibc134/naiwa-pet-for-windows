#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
原创占位角色「蛋蛋」生成器 —— 让 clone 仓库的人不准备任何素材也能开箱即玩。

为什么需要它：本仓库出于版权原因不附带任何角色素材（见 README 版权说明），
但"clone 下来跑不起来"对开源项目很不友好。这个脚本用纯代码画出一个
**完全原创**的浅蓝蛋形小生物（和任何第三方角色无关），并合成它的笑声、
三句示例语音与逐帧口型曲线，生成整套 assets/pet/ 运行素材。

生成后：
    python src/main.py          # 蛋蛋立刻出现在桌面上
    python tools/make_placeholder.py --gif docs/demo.gif   # 重录 README 演示图

用法:
    python tools/make_placeholder.py                 # 写入 assets/pet/
    python tools/make_placeholder.py --out 某目录     # 写到别处（自检用）
    python tools/make_placeholder.py --force          # 覆盖已有 idle.webp

画布与真实工程一致（560x827），所以换回自备素材时尺寸逻辑完全相同。
"""
import argparse
import json
import math
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CANVAS = (560, 827)
SS = 2                       # 超采样倍数，缩回来得到平滑边缘
FPS = 30

# —— 原创配色：浅蓝蛋形 + 奶油肚皮 + 深青触角/脚 ——
BODY = (126, 196, 232)
DARK = (88, 158, 208)
BELLY = (248, 243, 228)
EYE_W = (255, 255, 255)
PUPIL = (38, 40, 52)
MOUTH = (70, 45, 55)
MOUTH_DARK = (46, 26, 34)
TONGUE = (226, 122, 132)


# ------------------------------------------------------------------ 绘制
def _new():
    return Image.new("RGBA", (CANVAS[0] * SS, CANVAS[1] * SS), (0, 0, 0, 0))


def draw_character(dy=0.0, squash=0.0, blink=0.0, eyes_happy=False,
                   mouth_open=0.0, antenna_wag=0.0, arm_raise=0.0):
    """画一帧蛋蛋。所有参数都是"相对量"，方便动画复用。

    dy         整体上下位移（浮动/弹跳）
    squash     挤压系数，>0 变矮胖（大笑节奏）
    blink      眼睑盖住眼睛的比例 0~1
    eyes_happy 用弯弯的闭眼弧线代替圆眼（笑）
    mouth_open 张嘴高度比例 0~1
    antenna_wag 触角摆幅 -1~1
    arm_raise  手臂上抬比例 0~1
    """
    s = SS
    img = _new()
    d = ImageDraw.Draw(img)
    cx, cy = 280 * s, (500 + dy) * s
    rx = 185 * s * (1 + 0.05 * squash)
    ry = 270 * s * (1 - 0.06 * squash)

    # 脚（先画，被身体压住一半）
    for fx in (-65, 65):
        d.ellipse([(cx + fx * s) - 55 * s, (cy + 245 * s) - 26 * s,
                   (cx + fx * s) + 55 * s, (cy + 245 * s) + 26 * s], fill=DARK)
    # 手臂（同色，叠在身体边上自然融合；笑时上抬）
    for side in (-1, 1):
        ax = cx + side * 182 * s
        ay = (cy + 45 * s) - arm_raise * 34 * s
        d.ellipse([ax - 42 * s, ay - 26 * s, ax + 42 * s, ay + 26 * s], fill=BODY)
    # 触角
    tipx = cx + antenna_wag * 14 * s
    tipy = cy - ry - 42 * s
    d.line([cx, cy - ry + 6 * s, tipx, tipy + 12 * s], fill=DARK, width=int(6 * s))
    d.ellipse([tipx - 15 * s, tipy - 15 * s, tipx + 15 * s, tipy + 15 * s], fill=DARK)
    # 身体 + 肚皮
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=BODY)
    d.ellipse([cx - 118 * s, cy + 60 * s - 148 * s,
               cx + 118 * s, cy + 60 * s + 148 * s], fill=BELLY)
    # 眼睛
    for ex in (-68, 68):
        x = cx + ex * s
        y = cy - 170 * s
        r = 36 * s
        if eyes_happy:
            d.arc([x - r, y - r * 0.5, x + r, y + r * 1.1],
                  start=200, end=340, fill=PUPIL, width=int(6 * s))
            continue
        d.ellipse([x - r, y - r, x + r, y + r], fill=EYE_W)
        pr = 17 * s
        px = x + (6 * s if ex < 0 else -6 * s)
        d.ellipse([px - pr, y - pr, px + pr, y + pr], fill=PUPIL)
        hr = 6 * s
        d.ellipse([px - hr * 1.6, y - hr * 1.9, px + hr * 0.2, y - hr * 0.1],
                  fill=EYE_W)
        if blink > 0.01:                      # 眼睑：用体色从上往下盖
            cover = 2 * r * blink
            d.rectangle([x - r - s, y - r - 2 * s, x + r + s, y - r + cover],
                        fill=BODY)
            d.line([x - r, y - r + cover, x + r, y - r + cover],
                   fill=DARK, width=int(3 * s))
    # 嘴：闭口是一条微笑弧线，张口是暗色椭圆 + 舌头
    # （放在肚皮上沿之上，不然笑弧会和肚皮边线叠在一起）
    my = cy - 108 * s
    if mouth_open < 0.06:
        d.arc([cx - 32 * s, my - 14 * s, cx + 32 * s, my + 16 * s],
              start=20, end=160, fill=MOUTH, width=int(5 * s))
    else:
        oh = (5 + 30 * mouth_open) * s
        ow = (30 + 8 * mouth_open) * s
        d.ellipse([cx - ow, my - 4 * s, cx + ow, my - 4 * s + 2 * oh],
                  fill=MOUTH_DARK)
        if mouth_open > 0.45:
            th = oh * 0.42
            d.ellipse([cx - ow * 0.62, my - 4 * s + oh * 1.18 - th,
                       cx + ow * 0.62, my - 4 * s + oh * 1.18 + th * 0.6],
                      fill=TONGUE)
    return img.resize(CANVAS, Image.LANCZOS)


def frames_idle(n=45):
    return [draw_character(dy=5.0 * math.sin(2 * math.pi * i / n)) for i in range(n)]


def frames_blink():
    return [draw_character(blink=t) for t in (0.25, 0.5, 0.75, 1.0, 0.5)]


def frames_talk():
    return [draw_character(mouth_open=t)
            for t in (0.18, 0.34, 0.50, 0.66, 0.82, 1.0)]


def frames_laugh(n=48):
    out = []
    for i in range(n):
        p = i / n
        bounce = -30 * abs(math.sin(2 * math.pi * 2 * p))
        squash = math.sin(2 * math.pi * 2 * p + math.pi / 2)
        mo = 0.55 + 0.45 * abs(math.sin(2 * math.pi * 4 * p))
        out.append(draw_character(dy=bounce, squash=squash, eyes_happy=True,
                                  mouth_open=mo, antenna_wag=math.sin(2 * math.pi * 3 * p),
                                  arm_raise=0.6 + 0.4 * math.sin(2 * math.pi * 2 * p)))
    return out


# ------------------------------------------------------------------ 音频
def write_wav(path, x, sr=44100, peak_db=-3.0):
    x = np.asarray(x, dtype=np.float64)
    x = x / (np.abs(x).max() + 1e-12) * (10 ** (peak_db / 20))
    n = int(0.008 * sr)
    x[:n] *= np.linspace(0, 1, n)
    x[-n:] *= np.linspace(1, 0, n)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def synth_laugh(sr=44100):
    """五声"哈-哈-哈-哈-哈"：下滑基频 + 噪声成分 + 快起慢落包络。"""
    t = np.arange(int(sr * 1.6)) / sr
    out = np.zeros_like(t)
    rng = np.random.RandomState(7)
    for k in range(5):
        t0 = 0.05 + k * 0.28
        seg = (t >= t0) & (t < t0 + 0.24)
        env = np.clip(1 - (t - t0) / 0.22, 0, 1)[seg] ** 1.4
        f0 = 320 - 55 * k
        tau = (t - t0)[seg]
        vib = 1 + 0.07 * np.sin(2 * np.pi * 9 * tau)
        tone = 0.6 * np.sign(np.sin(2 * np.pi * f0 * vib * tau)) * 0.5
        tone += 0.4 * np.sin(2 * np.pi * f0 * 2 * vib * tau)
        out[seg] += (tone + 0.3 * rng.randn(seg.sum())) * env
    return out, sr


def synth_voice(pattern, sr=44100):
    """按 (频率Hz, 时长s, 间隙s) 序列合成"电子音节"，作为示例语音。"""
    parts = []
    rng = np.random.RandomState(3)
    for f0, dur, gap in pattern:
        n = int(dur * sr)
        tau = np.arange(n) / sr
        env = np.clip(np.minimum(tau / 0.02, (dur - tau) / 0.06), 0, 1)
        glide = f0 * (1 + 0.12 * np.sin(2 * np.pi * 5 * tau))
        x = 0.55 * np.sin(2 * np.pi * glide * tau) + 0.25 * np.sin(2 * np.pi * glide * 2 * tau)
        x += 0.18 * rng.randn(n)
        parts.append(x * env)
        if gap > 0:
            parts.append(np.zeros(int(gap * sr)))
    return np.concatenate(parts), sr


def open_curve(x, sr, fps=FPS):
    """与 tools/make_voice.py 同一套逻辑：RMS -> 归一化 -> 快攻慢放 -> 伽马1.8。"""
    hop = sr / fps
    n = max(1, int(len(x) / hop))
    rms = np.array([np.sqrt(np.mean(x[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12)
                    for i in range(n)])
    db = 20 * np.log10(rms + 1e-12)
    lo, hi = np.percentile(db, 15), np.percentile(db, 97)
    v = np.clip((db - lo) / max(1.0, hi - lo), 0, 1) ** 1.8
    out = np.empty_like(v)
    cur = 0.0
    for i, t in enumerate(v):
        cur += (t - cur) * (0.62 if t > cur else 0.30)
        out[i] = cur
    edge = min(3, len(out) // 4) if len(out) > 8 else 0
    if edge:
        out[:edge] *= np.linspace(0, 1, edge)
        out[-edge:] *= np.linspace(1, 0, edge)
    return out


VOICES = [
    ("demo1", "咕咕哒", [(523, 0.16, 0.05), (659, 0.18, 0.06), (784, 0.26, 0.0)]),
    ("demo2", "嘀嘀哒", [(880, 0.10, 0.05), (880, 0.10, 0.05), (587, 0.30, 0.0)]),
    ("demo3", "咕噜噜", [(392, 0.20, 0.04), (330, 0.20, 0.04), (294, 0.32, 0.0)]),
]


# ------------------------------------------------------------------ 演示 GIF
def make_gif(out_path, talk_curve):
    w, h = 300, 443
    bg = np.zeros((h, w, 3), np.uint8)
    grad = np.linspace(235, 200, h)[:, None]        # (h,1) 广播到 (h,w)
    bg[..., 0] = grad
    bg[..., 1] = grad * 1.02
    bg[..., 2] = np.clip(grad * 1.08, 0, 255)
    bg_img = Image.fromarray(bg, "RGB")

    def comp(fr, dy=0.0):
        canvas = bg_img.copy()
        im = fr.resize((w, int(h * CANVAS[1] / CANVAS[0] * 0.537)), Image.LANCZOS)
        canvas.paste(im, (int((w - im.width) / 2), int((canvas.height - im.height) / 2 + dy * w / CANVAS[0] * 2)), im)
        return canvas.convert("P", palette=Image.ADAPTIVE, colors=128)

    frames = []
    for f in frames_idle(45)[:34]:
        frames.append(comp(f))
    for f in frames_blink():
        frames.append(comp(f))
    for f in frames_idle(45)[34:]:
        frames.append(comp(f))
    for f in frames_laugh():
        frames.append(comp(f))
    talk = frames_talk()
    for v in talk_curve:
        idx = min(len(talk) - 1, int(round(v * (len(talk) - 1))))
        frames.append(comp(talk[idx] if v > 0.1 else draw_character()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(out_path, save_all=True, append_images=frames[1:],
                   duration=int(1000 / 15), loop=0, optimize=True)
    return len(frames)


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "assets" / "pet"))
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的 idle.webp（默认拒绝，防止误删真素材）")
    ap.add_argument("--gif", default=None, help="顺便生成演示 GIF 的路径")
    args = ap.parse_args()

    out = Path(args.out)
    if (out / "idle.webp").exists() and not args.force:
        raise SystemExit("检测到已有 idle.webp —— 那可能是你的真素材。"
                         "确认要覆盖请加 --force，或换 --out 目录。")
    for sub in ("laugh", "blink", "talk", "voice"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    def save(fr, path):
        fr.save(path, "WEBP", lossless=False, quality=92, method=4, exact=True)

    save(draw_character(), out / "idle.webp")
    for i, f in enumerate(frames_blink()):
        save(f, out / "blink" / ("%02d.webp" % i))
    for i, f in enumerate(frames_talk()):
        save(f, out / "talk" / ("%02d.webp" % i))
    for i, f in enumerate(frames_laugh()):
        save(f, out / "laugh" / ("%04d.webp" % i))

    x, sr = synth_laugh()
    write_wav(out / "laugh.wav", x, sr)

    lines = []
    for vid, label, pattern in VOICES:
        vx, vsr = synth_voice(pattern)
        write_wav(out / "voice" / ("%s.wav" % vid), vx, vsr)
        curve = open_curve(vx, vsr)
        lines.append({"id": vid, "label": label, "file": "voice/%s.wav" % vid,
                      "duration": round(len(vx) / vsr, 3), "fps": FPS,
                      "open": [round(float(v), 3) for v in curve]})
    (out / "voice" / "curves.json").write_text(
        json.dumps({"lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")

    # 原创 exe 图标：真角色图标因版权不入库（见 .gitignore），占位模式顺手补一个，
    # 保证 build.bat 的 --icon 有东西可用。放在所有素材生成**之后**——图标只是
    # 锦上添花，坏了不该连累主流程；且只在写默认素材目录时才动 app.ico。
    if out.resolve() == (ROOT / "assets" / "pet").resolve():
        try:
            fr = draw_character()
            fh = 240
            fw = int(fr.width * fh / fr.height)
            small = fr.resize((fw, fh), Image.LANCZOS)
            canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            canvas.paste(small, ((256 - fw) // 2, 8), small)   # mask 必须与图同尺寸
            icon = ROOT / "assets" / "app.ico"
            canvas.save(icon, sizes=[(256, 256), (128, 128), (64, 64),
                                     (48, 48), (32, 32), (16, 16)])
            print("原创图标 -> %s" % icon)
        except Exception as exc:
            print("[警告] 图标生成失败（素材不受影响）: %s" % exc)

    print("已生成占位角色「蛋蛋」 -> %s" % out)
    print("  idle 1 帧, blink %d, talk %d, laugh %d, 语音 %d 句"
          % (len(frames_blink()), len(frames_talk()), len(frames_laugh()), len(lines)))

    # 自检：尺寸一致、透明背景、关键帧有内容差异
    base = Image.open(out / "idle.webp")
    assert base.size == CANVAS, "idle 尺寸不对"
    a = np.asarray(base.convert("RGBA"), dtype=np.uint8)
    assert a[..., 3].max() > 200 and a[0, 0, 3] == 0, "背景应透明/主体应有像素"
    t0 = np.asarray(Image.open(out / "talk" / "00.webp").convert("RGB"), dtype=np.int16)
    t5 = np.asarray(Image.open(out / "talk" / "05.webp").convert("RGB"), dtype=np.int16)
    assert np.abs(t0 - t5).max() > 60, "talk 首尾帧没有差异"
    assert (out / "laugh.wav").stat().st_size > 10000, "laugh.wav 太小"
    print("自检通过：画布 %dx%d、透明底、帧间有差异" % CANVAS)

    if args.gif:
        n = make_gif(Path(args.gif), lines[0]["open"])
        print("演示 GIF: %s (%d 帧, %.1f KB)"
              % (args.gif, n, Path(args.gif).stat().st_size / 1024))


if __name__ == "__main__":
    main()
