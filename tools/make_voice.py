#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把语音音频装进项目，并按音频能量算出每个时刻的"张嘴量"曲线。

为什么用曲线而不是固定循环：口型跟着音量走，重音处嘴张得大、气口处自然闭合，
看起来才像真的在说话。

输出：
  assets/pet/voice/<ascii>.wav      三句语音（44.1k / 16bit / 单声道）
  assets/pet/voice/curves.json      每句的 fps / 时长 / 张嘴量数组(0~1)

用法:
    python tools/make_voice.py --src "C:\\...\\我的语音"
"""
import argparse
import json
import shutil
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "pet" / "voice"
FPS = 30

# 文件名(ASCII，避免打包和 QUrl 的中文问题) -> 菜单显示文字 -> 源文件名
LINES = [
    ("gaga", "嘎嘎滴啦谁", "嘎嘎滴啦谁.wav"),
    ("houla", "猴啦刚吧得", "猴啦刚吧得.wav"),
    ("gajiu", "噶酒塞", "噶酒塞.wav"),
]


def read_wav(p):
    with wave.open(str(p), "rb") as w:
        sr, nch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        x = x.reshape(-1, nch).mean(axis=1)
    return sr, x


def open_curve(x, sr, fps=FPS):
    """按 1/fps 步长取 RMS，归一化到 0~1，再做快攻慢放平滑。"""
    hop = sr / fps
    n = int(len(x) / hop)
    rms = np.empty(n, dtype=np.float32)
    for i in range(n):
        s = int(i * hop)
        e = min(len(x), int((i + 1) * hop))
        seg = x[s:e]
        rms[i] = np.sqrt(np.mean(seg ** 2) + 1e-12) if len(seg) else 0.0
    db = 20 * np.log10(rms + 1e-12)
    lo = float(np.percentile(db, 15))          # 气口/静音
    hi = float(np.percentile(db, 97))          # 重音
    span = max(1.0, hi - lo)
    v = np.clip((db - lo) / span, 0.0, 1.0)
    # 伽马 > 1：把中低音量压下去。用 < 1 会让整句都停在"大张"档，
    # 看起来像一直在喊；说话应当是音节之间真的合上。
    v = v ** 1.8

    # 快攻慢放：张嘴跟随快，闭合稍慢，避免抖动
    out = np.empty_like(v)
    cur = 0.0
    for i, t in enumerate(v):
        k = 0.62 if t > cur else 0.30
        cur += (t - cur) * k
        out[i] = cur
    # 首尾收到 0，保证进出自然
    edge = min(3, len(out) // 4) if len(out) > 8 else 0
    if edge:
        out[:edge] *= np.linspace(0, 1, edge)
        out[-edge:] *= np.linspace(1, 0, edge)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="成品 wav 所在目录")
    args = ap.parse_args()
    src = Path(args.src)
    OUT.mkdir(parents=True, exist_ok=True)

    lines = []
    print("%-8s %-12s %8s %6s %8s" % ("id", "显示名", "时长s", "帧数", "张嘴均值"))
    for key, label, fname in LINES:
        p = src / fname
        if not p.exists():
            raise SystemExit("找不到音频: %s" % p)
        shutil.copyfile(p, OUT / (key + ".wav"))
        sr, x = read_wav(p)
        curve = open_curve(x, sr)
        dur = len(x) / sr
        lines.append({
            "id": key,
            "label": label,
            "file": "voice/%s.wav" % key,
            "duration": round(dur, 3),
            "fps": FPS,
            "open": [round(float(v), 3) for v in curve],
        })
        print("%-8s %-12s %8.2f %6d %8.3f"
              % (key, label, dur, len(curve), float(curve.mean())))

    data = {"lines": lines}
    (OUT / "curves.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写 %s" % (OUT / "curves.json"))
    print("音频 -> %s" % OUT)


if __name__ == "__main__":
    main()
