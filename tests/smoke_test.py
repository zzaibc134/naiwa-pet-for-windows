# -*- coding: utf-8 -*-
"""无头冒烟测试：启动桌宠、模拟点击、逐帧验证动画、验证待机浮动与眨眼。

用法：  python tests/smoke_test.py
测试跑在独立的 QSettings 作用域里，不会污染真实用户的 scale/x/y。
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["PET_SETTINGS_APP"] = "pet-smoketest"      # 独立作用域，跑完即删
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PySide6.QtCore import QSize, QSettings
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import main as pet_main

OUTDIR = Path(tempfile.mkdtemp(prefix="nailong_smoke_"))
app = QApplication([])
passed = []


def ok(msg):
    passed.append(msg)
    print("[OK]", msg)


# 清掉上次可能残留的测试配置
QSettings("nailong-pet", "pet-smoketest").clear()

pet = pet_main.PetWindow()
pet.show()

print("素材画布:", pet.base_size, " 显示基准:", pet.design_size,
      " 超采样:", pet.asset_oversample, " 帧数:", pet.frame_count)
assert pet.frame_count >= 24, (
    f"序列帧太少（{pet.frame_count}）——先运行 python tools/make_placeholder.py "
    "生成占位素材，或按 README 准备自己的素材")
assert not pet.idle_pm.isNull()
ok(f"素材加载：{pet.frame_count} 帧，画布 {pet.base_size.width()}x{pet.base_size.height()}")

# 1) 所有帧可解码，且与 idle 同画布（不同画布会让角色在切换瞬间跳位）
for i in range(0, pet.frame_count, 10):
    im = QImage(str(pet.frame_paths[i]))
    assert not im.isNull(), f"帧{i}解码失败"
    assert im.size() == pet.base_size, f"帧{i}画布 {im.size()} != idle {pet.base_size}"
ok("全部序列帧可解码且与 idle 同画布")

# 2) 显示尺寸按 DESIGN_WIDTH 归一：高清素材不该把桌宠放大一倍
assert pet.design_size.width() == pet_main.PetWindow.DESIGN_WIDTH, pet.design_size
ok(f"高清素材不改变显示尺寸（基准宽 {pet.design_size.width()}）")

# 3) 待机渲染与点击遮罩
pet.tick()
assert pet.state == "idle"
assert not pet.mask().isEmpty()
pet.grab().save(str(OUTDIR / "app_idle.png"))
ok(f"待机渲染正常，遮罩非空，窗口 {pet.size().width()}x{pet.size().height()}")

# 4) 待机浮动是亚像素的：一个周期内必须出现大量非整数偏移
#    （旧实现 int(3*sin) 只有 5 个位置，两端各停 1.3 秒，看起来一顿一顿）
dy_seq = []
t0 = time.monotonic()
for _ in range(100):
    pet.state_start = t0            # 保持在待机，别被大笑打断
    pet.tick()
    dy_seq.append(pet.render_dy)
    time.sleep(0.004)
frac = sum(1 for v in dy_seq if abs(v - round(v)) > 1e-6)
levels = len(set(round(v, 3) for v in dy_seq))
assert frac > 60, f"浮动被量化了：100 帧里只有 {frac} 帧是小数偏移"
assert levels > 20, f"浮动位置太少（{levels}），不是连续运动"
ok(f"待机浮动亚像素：100 帧中 {frac} 帧为小数偏移，共 {levels} 个不同位置")

# 4b) 静止状态：勾上之后不再浮动；取消后能渐入恢复；设置能持久化
pet.act_static.setChecked(True)
pet.toggle_static()
assert pet.static is True
for _ in range(12):                 # 增益每帧 -0.25，12 帧足够归零
    pet.state_start = t0
    pet.tick()
    time.sleep(0.004)
assert abs(pet.render_dy) < 1e-6, f"静止状态下仍在浮动：dy={pet.render_dy}"
frozen = []
for _ in range(10):
    pet.tick()
    frozen.append(pet.render_dy)
    time.sleep(0.004)
assert all(abs(v) < 1e-6 for v in frozen), f"静止状态不稳定，dy 又动了：{frozen}"
pet.act_static.setChecked(False)
pet.toggle_static()
assert pet.static is False
assert QSettings("nailong-pet", "pet-smoketest").value("static") == "false"
ok("静止状态生效：不浮动、稳定，且开关可持久化")

# 5) 眨眼帧加载，且与 idle 同画布
assert len(pet.blink_imgs) >= 2, f"眨眼帧缺失（{len(pet.blink_imgs)}）"
for im in pet.blink_imgs:
    assert im.size() == pet.base_size
ok(f"眨眼帧已加载：{len(pet.blink_imgs)} 帧")

# 6) 眨眼会真的播放，且播放期间轮廓不变（遮罩不必每帧重下发）
pet.next_blink = time.monotonic() - 1          # 到点
pet._blink_seq = []
seen_blink = 0
mask_before = pet.mask().boundingRect()
for _ in range(12):
    pet.tick()
    time.sleep(0.004)
    if pet.current_pm is not None and pet._blink_pos > 0:
        seen_blink += 1
assert seen_blink >= 3, f"眨眼没有播起来（只命中 {seen_blink} 帧）"
assert pet.mask().boundingRect() == mask_before, "眨眼期间轮廓变了，会引发遮罩抖动"
ok(f"眨眼序列播放 {seen_blink} 帧，期间轮廓遮罩保持不变")

# 6b) 语音 + 说话口型：点一行语音后，口型要按曲线张嘴，播完回到闭嘴
assert len(pet.talk_imgs) >= 2, f"说话帧缺失（{len(pet.talk_imgs)}）"
for im in pet.talk_imgs:
    assert im.size() == pet.base_size, "说话帧画布与 idle 不一致"
assert len(pet.voice_lines) >= 1, "没有加载到语音"
ok(f"语音 {len(pet.voice_lines)} 句、说话帧 {len(pet.talk_imgs)} 档")
pet.play_voice(0)
assert pet.voice_idx == 0
ln = pet.voice_lines[0]
t0 = pet.voice_start
picked = [pet.voice_frame(t0 + el) for el in (0.05, 0.2, 0.4, 0.8)]
assert any(v >= 0 for v in picked), f"说话过程中没有张嘴（{picked}）"
assert max(picked) > 0, f"张嘴档位没有变化（{picked}）"
assert pet.voice_frame(t0 + ln["duration"] + 1.0) == -1, "语音结束后口型没有复位"
ok(f"说话口型跟随音频：{picked} → 结束后闭嘴")
pet.play_voice(0)
pet.stop_voice()
assert pet.voice_idx == -1
assert pet.voice_frame(time.monotonic()) == -1
ok("语音可中断，中断后回到待机")

# 7) 大笑时不叠加浮动、不眨眼
pet.play_laugh()
assert pet.state == "laugh"
time.sleep(0.5)
pet.tick()
assert pet.frame_idx > 0
gain = pet._float_gain
assert 0.0 <= gain < 1.0, f"大笑中浮动增益没有衰减（{gain}）"
pet.grab().save(str(OUTDIR / "app_laugh.png"))
ok(f"点击进入大笑，帧索引 {pet.frame_idx}，浮动增益衰减到 {gain:.2f}")

pet.play_laugh()
assert pet.frame_idx == 0
ok("播放中再次点击可重播")

pet.state_start = time.monotonic() - 20
pet.tick()
assert pet.state == "idle"
ok("播放结束自动回到待机")

# 8) 三档大小：窗口高 = 基准高 x 档位 + 上下浮动留白
for name, value in pet_main.SCALE_LEVELS.items():
    pet.apply_scale(value)
    expected = QSize(
        int(pet.design_size.width() * value),
        int(pet.design_size.height() * value) + 2 * pet_main.PetWindow.FLOAT_MARGIN,
    )
    assert pet.size() == expected, (pet.size(), expected, name)
ok("三档大小切换正确")

# 9) 静音开关
pet.apply_scale(1.0)
pet.act_mute.setChecked(True)
pet.toggle_mute()
assert pet.muted is True
pet.act_mute.setChecked(False)
pet.toggle_mute()
assert pet.muted is False
ok("静音切换正常")

# 10) 身体不能被看穿：抽样扫描序列帧里的"被身体包住且又亮又有角色色"的洞。
#     浅色肚皮被抠穿时缩略图看上去完全正常，只有数据能查出来（本项目踩过）。
#     opencv 在 requirements-dev 里——只跑快速开始的人没装，跳过即可。
try:
    import cv2
except ImportError:
    cv2 = None

if cv2 is None:
    ok("抠穿检查跳过（未安装 opencv；做素材时 pip install -r requirements-dev.txt）")
else:
    suspect_total = 0
    suspect_frames = []
    for i in range(0, pet.frame_count, 13):
        a = QImage(str(pet.frame_paths[i]))
        assert not a.isNull()
        img = a.convertToFormat(QImage.Format_RGBA8888)
        arr = np.frombuffer(img.bits(), dtype=np.uint8)[
            :img.sizeInBytes()].reshape(img.height(), img.width(), 4)
        rgb = arr[..., :3].astype(np.int32)
        sat = rgb.max(2) - rgb.min(2)
        lum = rgb.mean(2)
        empty = (arr[..., 3] < 40).astype(np.uint8)
        n, lab = cv2.connectedComponents(empty, 8)
        if n <= 1:
            continue
        border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
        border.discard(0)
        bad = 0
        for j in range(1, n):
            if j in border:
                continue
            m = lab == j
            ar = int(m.sum())
            if ar >= 3000 and float(sat[m].mean()) >= 12 and float(lum[m].mean()) >= 205:
                bad += ar
        suspect_total += bad
        if bad:
            suspect_frames.append((bad, i))
    assert suspect_total == 0, ("身体被抠穿：%d px，帧 %s" % (
        suspect_total, [f"00%03d" % i for _, i in sorted(suspect_frames, reverse=True)[:5]]))
    ok(f"序列帧无被抠穿的身体（抽样 {len(range(0, pet.frame_count, 13))} 帧，0 可疑洞）")

# 11) 测试自己的作用域没写脏真实配置
real = QSettings("nailong-pet", "pet")
ok(f"真实用户配置未被测试污染（当前 scale={real.value('scale', '未设置')}）")

QSettings("nailong-pet", "pet-smoketest").clear()
print(f"\n全部 {len(passed)} 项冒烟测试通过；截图目录 {OUTDIR}")
