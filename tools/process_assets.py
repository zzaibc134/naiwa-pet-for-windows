#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌宠素材预处理脚本：白底录屏视频 -> 透明 WebP 序列帧 + 待机帧 + wav 音效

典型用法（大笑录屏视频）:
    python tools/process_assets.py input.mp4 \
        --action-start 0 --action-end 17 \
        --idle-time 17.5 \
        --crop 80:310:560:940 \
        --width 560 \
        --key white --similarity 0.02 --blend 0.05 \
        --audio \
        --out assets/pet

关于 --similarity / --blend：从小的开始往上加。角色身上有大片浅色
（奶油肚皮、白眼睛、白牙）时，这两个值稍大就会把浅色一起抠掉，
再被地面阴影泛洪当桥灌进身体，肚皮就会出现上万像素的大洞（本项目踩过）。
脚本会在处理完每一帧后统计"被身体包住的空洞"，超过阈值直接中止。

关于 --width：取"显示宽度的 2 倍"。桌宠在 100% 缩放下标准档显示 280 宽，
125%/150% 高分屏上是 350~420 物理像素；素材只给 280 就会被放大显示，边缘
阶梯全部暴露。给 560 后显示端是缩小采样，抗锯齿羽化才有东西可平均。
运行时会按素材宽度 / 280 自动归一化显示尺寸，所以换高清素材不会把桌宠变大。

依赖: ffmpeg(命令行)、Pillow、numpy、opencv-python
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageFilter


def run(cmd):
    print("  $", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True)


def extract_frames(video, start, end, crop, key, similarity, blend, out_dir, fps=30):
    """用 ffmpeg 裁剪 + colorkey 抠白，导出带 alpha 的 PNG 序列帧"""
    x, y, w, h = crop
    vf = f"crop={w}:{h}:{x}:{y}"
    if key:
        vf += f",colorkey={key}:{similarity}:{blend}"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(start), "-to", str(end),
        "-i", video,
        "-vf", vf,
        "-r", str(fps),
        "-pix_fmt", "rgba",
        os.path.join(out_dir, "frame_%05d.png"),
    ]
    run(cmd)


def extract_single_frame(video, t, crop, key, similarity, blend, out_path):
    x, y, w, h = crop
    vf = f"crop={w}:{h}:{x}:{y}"
    if key:
        vf += f",colorkey={key}:{similarity}:{blend}"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", str(t),
        "-i", video,
        "-vf", vf,
        "-frames:v", "1",
        "-pix_fmt", "rgba",
        out_path,
    ]
    run(cmd)


def remove_white_shadow(rgba: np.ndarray) -> None:
    """
    从画面底边缘做泛洪填充，找出"与画面外部连通"的背景区域，把该区域
    内低饱和、中高明度的灰色阴影像素清除。角色内部的浅色部位（肚皮、
    牙齿、眼白）被实体完全包围，填充无法到达，不会误删；噪点阻挡和
    被脚部分遮挡的阴影也能正确处理。
    """
    import cv2
    h, w, _ = rgba.shape
    rgb = rgba[..., :3].astype(np.int32)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    a = rgba[..., 3]
    occupied = a > 0
    is_shadow = occupied & ((mx - mn) < 35) & (mx > 150)

    # 可通行图：透明或阴影=255，实体=0；底部加一行全可通行作为种子行
    walk = (~occupied | is_shadow).astype(np.uint8) * 255
    walk = np.vstack([walk, np.full((1, w), 255, dtype=np.uint8)])
    mask = np.zeros((h + 3, w + 2), np.uint8)
    cv2.floodFill(walk, mask, (w // 2, h), 128, loDiff=0, upDiff=0)
    outside = walk[:h] == 128
    # 只清 alpha，保留 RGB：泛洪有可能顺浅色边缘灌进肚皮（下面的
    # fill_interior_holes 会按颜色把误清的实体还原），若把 RGB 一起抹成 0，
    # 还原回来的就是一块黑色。
    rgba[outside & is_shadow, 3] = 0


def remove_warm_ground_shadow(rgba: np.ndarray, band_top: float = 0.85,
                              sat_max: int = 70, mx_min: int = 150) -> None:
    """
    第二阶段：清除脚底残留的"暖色地面阴影块"。

    remove_white_shadow 只认中性灰阴影（饱和差 < 35）。但录屏里的地面接触
    阴影本身偏暖米色，再经 h264 宏块量化，会成一整块饱和差 28~60 的浅粉米
    色方块，正好躲过那条判据，于是脚底留下斑点。

    这里不能简单放宽全局判据：colorkey 的 blend 会在角色边缘生成一圈半透明
    白色光晕，那些光晕同样是低饱和高亮度，一旦算作"可通行阴影"，泛洪就会顺
    着光晕漏进角色内部，把奶油色肚皮整个吃掉（实测清除量从每帧 128 暴涨到
    3986 像素，且多在画面上半部）。所以把搜索范围限制在画面底部 band_top 以下，
    那里没有肚皮，只有脚和地面。

    亮度 <= mx_min 的深色接触阴影刻意保留：那是让角色"站在地上"的必要线索。
    """
    import cv2
    h, w, _ = rgba.shape
    rgb = rgba[..., :3].astype(np.int32)
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    a = rgba[..., 3]
    occupied = a > 0
    zone = np.zeros((h, w), dtype=bool)
    zone[int(h * band_top):, :] = True
    is_shadow = occupied & ((mx - mn) < sat_max) & (mx > mx_min) & zone
    walk = (~occupied | is_shadow).astype(np.uint8) * 255
    walk = np.vstack([walk, np.full((1, w), 255, dtype=np.uint8)])
    mask = np.zeros((h + 3, w + 2), np.uint8)
    cv2.floodFill(walk, mask, (w // 2, h), 128, loDiff=0, upDiff=0)
    outside = walk[:h] == 128
    rgba[outside & is_shadow, 3] = 0      # 同上：只清 alpha，保留 RGB


def _interior_labels(rgba, thresh=40):
    """返回 (连通分量标号, 需要跳过的边界标号)。"""
    import cv2
    empty = (rgba[..., 3] < thresh).astype(np.uint8)
    n, lab = cv2.connectedComponents(empty, 8)
    border = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    border.discard(0)
    return n, lab, border


def interior_hole_split(rgba, pre, sat_min=12.0, thresh=40, solid_pre=200):
    """
    把"被身体包住的透明洞"按成因分成三类。

    判据用 pre（擦阴影之前的 alpha），不用颜色：
      * pre >= solid_pre 的洞 = 泛洪把实心身体抹掉了 → **真缺陷**，能补回来；
      * pre 很小的洞 = 抠像本来就没让它实心 → 那是**该透明的缝隙**（两腿之间、
        手臂与肚皮之间那些在阴影里的暖色缝）；
      * pre 很小但面积大、又亮又有角色色的洞 = colorkey 自己把浅色身体抠没了
        → 补不回来（原色已被当成背景处理），只能报"参数太宽松"。

    为什么不能用颜色分：实测手臂下方那条阴影缝平均饱和差 65、均色
    (176,147,111)，比奶油肚皮（饱和差 40、均色 (251,236,211)）还"有颜色"；
    但两者的 pre 分别是 42 和 255，一眼分开。

    返回 (缺陷洞, 合法洞, 疑似抠穿) 三类像素数。
    """
    import numpy as np
    n, lab, border = _interior_labels(rgba, thresh)
    rgb = rgba[..., :3].astype(np.int32)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    lum = rgb.mean(axis=2)
    defect = legit = suspect = 0
    for i in range(1, n):
        if i in border:
            continue
        m = lab == i
        ar = int(m.sum())
        if ar < 25:
            continue
        if float(pre[m].mean()) >= solid_pre:
            defect += ar
        elif ar >= 3000 and float(sat[m].mean()) >= sat_min and float(lum[m].mean()) > 205:
            suspect += ar
        else:
            legit += ar
    return defect, legit, suspect


def fill_interior_holes(rgba, pre, solid_pre=200, thresh=40):
    """
    把"被身体包住、且泛洪之前是实心"的透明洞补回不透明。

    colorkey 只改 alpha、不改 RGB，所以这些像素的颜色还是视频里的原色 ——
    补回 alpha 就原样还原，不需要猜颜色。

    这一步是必需的：擦地面阴影的泛洪会顺浅色边缘灌进角色内部，实测把奶油
    肚皮抠出 13,000~19,000 像素的大洞，而缩略图看上去完全正常，只有放到有
    内容的背景上才看得出来。
    """
    import numpy as np
    n, lab, border = _interior_labels(rgba, thresh)
    filled = 0
    for i in range(1, n):
        if i in border:
            continue
        m = lab == i
        if float(pre[m].mean()) < solid_pre:
            continue                     # 抠像本来就没实心：那是该透明的缝隙
        rgba[m, 3] = 255
        filled += int(m.sum())
    return filled


def erode_alpha(rgba, px: int, ellipse: bool = True) -> None:
    """
    内缩 alpha 去掉 colorkey 留下的白边。

    一定要用椭圆核：PIL 的 MinFilter 是方形结构元，对曲线轮廓腐蚀会在斜边和
    曲线上啃出一格一格的方角缺口（桌宠边缘那种"割裂纹"就是它）。实测把折角
    密度从每 1000 像素周长 11.2 个降到 8.4 个。
    """
    import cv2
    a = rgba[..., 3]
    if ellipse:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
        rgba[..., 3] = cv2.erode(a, k)
    else:
        img = Image.fromarray(a, mode="L").filter(ImageFilter.MinFilter(2 * px + 1))
        rgba[..., 3] = np.asarray(img, dtype=np.uint8)


def feather_alpha(rgba, sigma: float = 0.8) -> None:
    """
    给 alpha 做一次高斯：不透明区卷积后仍是不透明，透明区仍是透明，只有边缘
    1~2px 被拉成渐变。素材若是二值硬边，显示时被放大一点点就会露出锯齿台阶；
    有了这圈渐变，Qt 的缩放就能把它渲染成连续过渡。
    """
    import cv2
    a = rgba[..., 3].astype(np.float32)
    k = max(3, int(sigma * 6) | 1)
    rgba[..., 3] = np.clip(cv2.GaussianBlur(a, (k, k), sigma), 0, 255).astype(np.uint8)


def spread_opaque_color(rgba, iters: int = 8) -> None:
    """
    把不透明像素的颜色推进透明区，替代"透明像素 RGB 清零"。

    两个理由：
    1) 缩放时透明区颜色会被平均进边缘像素。塞黑色会把轮廓拉暗，留着抠掉的
       白色背景又会给角色裹一圈浅灰白毛边（就是那种"割裂"感）。推进角色自身
       颜色后，两种脏边都不会出现。
    2) WebP 的 4:2:0 色度会对邻域做平均，透明区留中性色比留纯白/纯黑更干净。
       注意 WebP 有损模式下的 alpha 通道本身是无损存储的，不需要额外参数。
    渲染时 alpha=0 的像素不参与合成，所以这一步只影响编码与缩放，不改变画面。
    """
    import cv2
    a = rgba[..., 3]
    rgb = rgba[..., :3].astype(np.float32)
    filled = a > 220
    k = np.ones((3, 3), np.uint8)
    for _ in range(iters):
        nb = cv2.dilate(filled.astype(np.uint8), k) > 0
        add = nb & ~filled
        if not add.any():
            break
        src = np.where(filled[..., None], rgb, 0.0)
        cnt = np.maximum(cv2.dilate(filled.astype(np.float32), k), 1.0)
        for ch in range(3):
            rgb[add, ch] = (cv2.dilate(src[..., ch], k)[add] / cnt[add])
        filled |= add
    rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)


def resize_rgba_premultiplied(rgba, w: int, h: int):
    """
    预乘 -> 缩放 -> 反预乘。

    PIL 的 resize 直接在非预乘 RGBA 上插值，会把透明区的颜色平均进半透明边缘，
    等于在轮廓上描一圈脏色。先预乘再缩放就没这个问题。
    """
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    pm = rgba[..., :3].astype(np.float32) * a
    tmp = np.dstack([pm, a * 255]).astype(np.uint8)
    r = np.array(Image.fromarray(tmp, "RGBA").resize((w, h), Image.LANCZOS),
                 dtype=np.float32)
    na = r[..., 3:4] / 255.0
    out = np.where(na > 1 / 255.0, r[..., :3] / np.maximum(na, 1e-6), 0.0)
    return np.dstack([np.clip(out, 0, 255).astype(np.uint8),
                      np.clip(na * 255, 0, 255).astype(np.uint8)])


def fix_edges(img: Image.Image, erode_px: int = 2, core_px: int = 6,
              feather_sigma: float = 0.8, stats=None) -> Image.Image:
    """
    修复 colorkey 抠像后的白边、地面阴影与角色内部误抠：
    1)  底部逐列泛洪清除与画面外连通的地面阴影；
    1b) 再清一遍脚底的暖色残留阴影块（见 remove_warm_ground_shadow）；
    2)  把泛洪误抹掉的实心身体补回不透明（按 pre 判定，见 fill_interior_holes）；
    3)  核心内的半透明像素补实（同样按 pre 判定）；
    4)  alpha 用椭圆核内缩 erode_px 去掉边缘白边（方形核会啃出方角）；
    5)  alpha 高斯羽化，把二值硬边变成 1~2px 抗锯齿渐变；
    6)  把角色颜色推进透明区，避免缩放/编码时脏色渗进轮廓。

    stats 传 dict 时会写入 holes（本帧补回的洞）、soft_alpha、residual_holes
    （残留缺陷洞）、legit_pockets（合法透明缝隙）、keyed_through（疑似抠穿）。
    """
    rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8).copy()
    # pre = 动任何一刀之前的 alpha。判断"这个洞是不是误伤"必须拿它当依据：
    # pre 高说明那里本来是实心身体，pre 低说明那里本来就该透。
    pre = rgba[..., 3].copy()

    # 1) 清除地面阴影（宽松泛洪：浅色高亮就擦，半透明也擦，否则脚边会留白）
    remove_white_shadow(rgba)
    # 1b) 再清一遍脚底的暖色残留阴影块
    remove_warm_ground_shadow(rgba)

    # 2) 把泛洪误抹掉的实心身体补回来（按 pre 判定，RGB 还是原色）
    holes = fill_interior_holes(rgba, pre)
    if stats is not None:
        stats["holes"] = holes

    # 3) 把"身体内部还半透明"的像素补实。
    #    核心必须用**当前** alpha 算，绝不能用 pre：地面阴影在抠像时本来是不透明的，
    #    用 pre 会把刚擦掉的阴影又整块填回来 —— 实测脚下多出 10,562 像素的
    #    卡其色块（均色 (168,146,116)），正是用户看到的那块"地面阴影"。
    #    阈值取 128 而不是 40：半透明像素不该算进核心，否则边缘会被糊厚。
    mask_img = Image.fromarray((rgba[..., 3] > 128).astype(np.uint8) * 255, mode="L")
    core_img = mask_img.filter(ImageFilter.MinFilter(2 * core_px + 1))
    core = np.asarray(core_img) > 0
    rgba[core, 3] = 255
    if stats is not None:
        aa = rgba[..., 3]
        stats["soft_alpha"] = int(((aa > 0) & (aa < 250)).sum())

    # 4) 椭圆核内缩去白边
    if erode_px > 0:
        erode_alpha(rgba, erode_px, ellipse=True)

    # 5) 抗锯齿羽化
    if feather_sigma > 0:
        feather_alpha(rgba, feather_sigma)

    # 6) 体检。必须在颜色外扩之前做：spread 会把角色色推进透明区，
    #    那时合法的腿间口袋也会变得"又大又亮又有角色色"，被误判成抠穿。
    #    alpha 到这里已是最终值，RGB 还是视频原色，正是判成因要的组合。
    if stats is not None:
        d, lg, sus = interior_hole_split(rgba, pre)
        stats["residual_holes"] = d
        stats["legit_pockets"] = lg
        stats["keyed_through"] = sus
        aa = rgba[..., 3]
        stats["soft_alpha"] = int(((aa > 0) & (aa < 250)).sum())

    # 7) 透明区颜色外扩（不清零，避免暗边/白边）
    spread_opaque_color(rgba)
    return Image.fromarray(rgba, mode="RGBA")


def main():
    ap = argparse.ArgumentParser(description="白底录屏视频 -> 透明 WebP 序列帧")
    ap.add_argument("video", help="输入视频路径")
    ap.add_argument("--action-start", type=float, default=0, help="动作段开始秒")
    ap.add_argument("--action-end", type=float, default=17, help="动作段结束秒")
    ap.add_argument("--idle-time", type=float, default=None, help="待机帧时间点(秒)")
    ap.add_argument("--crop", default="0:0:0:0", help="裁剪 x:y:w:h，去掉录屏 UI/水印")
    ap.add_argument("--width", type=int, default=280, help="输出宽度像素，按比例缩放")
    ap.add_argument("--key", default="white", help="抠像底色，如 white/0x00ff00，空字符串不抠")
    ap.add_argument("--similarity", type=float, default=0.02,
                    help="抠像容差：背景越接近纯白调越小，避免误抠角色浅色高光")
    ap.add_argument("--blend", type=float, default=0.05,
                    help="边缘过渡宽度，越大边缘越柔但越容易误抠")
    ap.add_argument("--audio", action="store_true", help="同时导出动作段音轨 wav")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--feather", type=float, default=0.8,
                    help="alpha 抗锯齿羽化 sigma，0 关闭")
    ap.add_argument("--quality", type=int, default=88, help="WebP 有损质量")
    ap.add_argument("--no-premul", dest="premul", action="store_false",
                    help="关闭预乘缩放（默认开启，可避免透明区颜色渗进轮廓）")
    ap.set_defaults(premul=True)
    ap.add_argument("--allow-holes", action="store_true",
                    help="即使检测到大量内部空洞也继续（默认中止）")
    ap.add_argument("--out", default="assets/pet", help="输出目录")
    args = ap.parse_args()

    crop = tuple(int(v) for v in args.crop.split(":"))
    assert len(crop) == 4, "--crop 格式为 x:y:w:h"
    if args.key == "":
        args.key = None

    out_dir = args.out
    laugh_dir = os.path.join(out_dir, "laugh")
    os.makedirs(laugh_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="pet_frames_") as tmp:
        fixed_dir = os.path.join(tmp, "fixed")
        os.makedirs(fixed_dir, exist_ok=True)

        print(f"[1/5] ffmpeg 抽取动作帧 ({args.action_start}-{args.action_end}s) ...")
        extract_frames(args.video, args.action_start, args.action_end, crop,
                       args.key, args.similarity, args.blend, tmp, args.fps)

        pngs = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        if not pngs:
            sys.exit("没有抽到任何帧，检查视频路径和裁剪参数")

        # 第一遍：修复白边，同时扫描全部帧的 alpha 并集包围盒。
        # 必须用全部帧（含躺地/伸手等极端姿态），否则后续帧会被裁断。
        print(f"[2/5] 修复白边并扫描全帧包围盒，共 {len(pngs)} 帧 ...")
        l0, t0, r0, b0 = 10**9, 10**9, -1, -1
        hole_frames, hole_total, worst_frame, worst_holes = 0, 0, None, 0
        thru_total, thru_frames = 0, 0
        for i, name in enumerate(pngs):
            st = {}
            im = fix_edges(Image.open(os.path.join(tmp, name)),
                           feather_sigma=args.feather, stats=st)
            resid = st.get("residual_holes", 0)
            thru = st.get("keyed_through", 0)
            thru_total += thru
            if thru:
                thru_frames += 1
            if resid > 0:
                hole_frames += 1
                hole_total += resid
                if resid > worst_holes:
                    worst_holes, worst_frame = resid, name
            box = im.getbbox()
            if box:
                l0, t0 = min(l0, box[0]), min(t0, box[1])
                r0, b0 = max(r0, box[2]), max(b0, box[3])
            im.save(os.path.join(fixed_dir, name), "PNG")
            im.close()
            if (i + 1) % 50 == 0:
                print(f"    {i+1}/{len(pngs)}", flush=True)
        # 空洞是这套管线最容易翻车的地方：浅色肚皮会被抠穿，而缩略图看上去
        # 完全正常，只有放到有内容的背景上才看得出来。必须显式报数。
        # residual = 泛洪误抹的实心身体（能补，补不上就是 bug）
        # keyed_through = colorkey 自己把浅色抠穿（补不回来，只能改参数）
        print(f"[2/5] 空洞体检：误伤残留 {hole_total} px（{hole_frames} 帧）"
              + (f"，最大 {worst_holes} 在 {worst_frame}" if worst_frame else "（干净）"))
        print(f"[2/5] 抠穿嫌疑 {thru_total} px（{thru_frames} 帧）"
              "—— colorkey 把浅色身体直接抠没了，补不回来")
        if thru_total > 2000:
            print("!! 警告：大面积浅色被抠穿，几乎一定是 --similarity/--blend 太宽松。")
            print("!! 本项目实测 --similarity 0.02 --blend 0.05 干净；"
                  "0.11/0.25 会抠出上万像素的肚皮洞。")
            if not args.allow_holes:
                sys.exit("已中止（确认素材本身就该透，可加 --allow-holes 继续）")
        if hole_frames > len(pngs) * 0.05 or worst_holes > 2000:
            print("!! 警告：泛洪误抹的实心身体没被补回来，管线有 bug，请检查 "
                  "fill_interior_holes 的 pre 判据。")
            if not args.allow_holes:
                sys.exit("已中止（确认洞是素材本身该有的，可加 --allow-holes 继续）")
        l0, t0 = max(0, l0 - 4), max(0, t0 - 4)
        r0, b0 = r0 + 4, b0 + 4
        bbox = (l0, t0, r0, b0)
        print(f"[3/5] 全帧统一包围盒: {bbox} ({r0-l0}x{b0-t0})")

        print("[4/5] 统一裁剪 + 缩放 -> WebP ...")
        frame_h = None
        for i, name in enumerate(pngs):
            im = Image.open(os.path.join(fixed_dir, name)).crop(bbox)
            arr = np.asarray(im.convert("RGBA"), dtype=np.uint8)
            h = round(im.height * args.width / im.width)
            if frame_h is None:
                frame_h = h
            out = (resize_rgba_premultiplied(arr, args.width, frame_h)
                   if args.premul else
                   np.asarray(im.resize((args.width, frame_h), Image.LANCZOS),
                              dtype=np.uint8))
            Image.fromarray(out, "RGBA").save(
                os.path.join(laugh_dir, f"{i:04d}.webp"),
                "WEBP", lossless=False, quality=args.quality, method=4, exact=True)
            im.close()

        # 待机帧：与动作帧完全相同的裁剪框和缩放，保证同一坐标系，切换不跳位
        if args.idle_time is not None:
            idle_png = os.path.join(tmp, "idle.png")
            print("[5/5] 抽取待机帧 ...")
            extract_single_frame(args.video, args.idle_time, crop,
                                 args.key, args.similarity, args.blend, idle_png)
            idle = fix_edges(Image.open(idle_png),
                             feather_sigma=args.feather).crop(bbox)
            arr = np.asarray(idle.convert("RGBA"), dtype=np.uint8)
            out = (resize_rgba_premultiplied(arr, args.width, frame_h)
                   if args.premul else
                   np.asarray(idle.resize((args.width, frame_h), Image.LANCZOS),
                              dtype=np.uint8))
            Image.fromarray(out, "RGBA").save(
                os.path.join(out_dir, "idle.webp"),
                "WEBP", lossless=False, quality=min(95, args.quality + 4),
                method=4, exact=True)
            idle.close()
        print(f"帧尺寸: {args.width}x{frame_h}")

    if args.audio:
        wav = os.path.join(out_dir, "laugh.wav")
        print("导出音轨 wav ...")
        run([
            "ffmpeg", "-y", "-v", "error",
            "-ss", str(args.action_start), "-to", str(args.action_end),
            "-i", args.video,
            "-vn", "-ac", "2", "-ar", "44100", "-sample_fmt", "s16",
            wav,
        ])

    total = len(os.listdir(laugh_dir))
    size = sum(os.path.getsize(os.path.join(laugh_dir, f))
               for f in os.listdir(laugh_dir)) / 1024 / 1024
    print(f"完成: {total} 帧, 动作序列 {size:.1f} MB -> {laugh_dir}")


if __name__ == "__main__":
    main()
