# -*- coding: utf-8 -*-
"""
桌宠主程序（PySide6）

功能：
- 透明无边框置顶窗口，不抢焦点，透明区域鼠标穿透
- 左键点击 = 播放捧腹大笑动作 + 笑声；按住拖动 = 移动位置
- 待机时轻微上下浮动（亚像素，不会一顿一顿）+ 随机眨眼
- 右键菜单：大小 / 静音 / 开机自启 / 隐藏(Alt+Shift+H) / 回到右下角 / 退出
- 系统托盘
- 素材为 assets/pet 下的 idle.webp、laugh/0000.webp...、blink/00.webp...、laugh.wav
  素材宽度是显示基准 DESIGN_WIDTH 的 2 倍，高分屏上按缩小采样显示，边缘更干净
"""
import json
import math
import os
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    Qt, QTimer, QSettings, QPoint, QSize, QRectF, QLockFile, QDir,
)
from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QPainter, QPixmap, QRegion, QIcon, QColor, QAction,
    QCursor, QImage, QActionGroup,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QSystemTrayIcon,
)

# ---------------------------------------------------------------- 资源路径

def resource_root() -> Path:
    """PyInstaller 打包后资源在 _MEIPASS；开发时在项目根目录"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


ASSET_DIR = resource_root() / "assets" / "pet"
FRAMES_DIR = ASSET_DIR / "laugh"

IS_WIN = sys.platform == "win32"


def log(msg: str) -> None:
    """打包成 --windowed 后 sys.stdout 是 None，直接 print 会抛异常。
    告警都在异常分支里，那里再崩就更难查了，所以统一走这个。"""
    try:
        print(msg)
    except Exception:
        pass


# 全局热键（仅 Windows）
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_SHIFT = 0x0004
VK_H = 0x48

SCALE_LEVELS = {"小": 0.75, "标准": 1.0, "大": 1.4}


# ---------------------------------------------------------------- 桌宠窗口

class PetWindow(QWidget):
    FPS_MS = 33
    FPS = 30
    # 待机浮动：幅度与偏移全程用浮点，不再 int() 量化成台阶。
    # 周期 5.0s 时平均每帧只走 0.17 物理像素，攒够 1 像素要 6 帧，
    # 看起来就是一顿一顿；改成 3.2s 后约每 3 帧走 1 像素，配合亚像素位移就顺了。
    IDLE_FLOAT_PX = 5.0
    FLOAT_PERIOD = 3.2
    # 窗口上下各留的余量（逻辑像素），保证 ±IDLE_FLOAT_PX 的亚像素位移不被裁掉
    FLOAT_MARGIN = 6
    # 显示尺寸基准：素材宽度按这个值归一化。素材换成 560 宽的高清版时，
    # 桌宠在屏幕上还是一样大，只是更清楚（变成缩小显示而不是放大显示）。
    DESIGN_WIDTH = 280
    # 眨眼：两次眨眼间隔 2.2~6 秒随机，15% 概率连眨两下
    BLINK_GAP_S = (2.2, 6.0)
    BLINK_DOUBLE_P = 0.15

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # 测试可用 PET_SETTINGS_APP 换到独立作用域，避免冒烟测试把真实用户的
        # scale/x/y 写脏（以前跑完测试，桌宠会变成"大"档且位置偏移）
        self.settings = QSettings(
            os.environ.get("PET_SETTINGS_ORG", "nailong-pet"),
            os.environ.get("PET_SETTINGS_APP", "pet"),
        )

        # 素材
        self.idle_pm = QPixmap(str(ASSET_DIR / "idle.webp"))
        frame_paths = sorted(FRAMES_DIR.glob("*.webp"))
        self.frame_paths = frame_paths
        self.frame_count = len(frame_paths)
        if self.idle_pm.isNull() or self.frame_count == 0:
            hint = ("未找到桌宠素材：\n" + str(ASSET_DIR) + "\n\n"
                    "第一次跑？一条命令生成原创占位角色「蛋蛋」：\n"
                    "    python tools/make_placeholder.py\n\n"
                    "想用自己的角色：按 README 的「素材 SOP」处理你的视频。")
            if sys.stderr is None:
                # 双击运行（pythonw / 打包版）：没有控制台，只能靠弹窗
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.critical(None, "素材缺失", hint)
            else:
                # 命令行 / 自动化流程：往 stderr 打，绝不弹模态框卡住无人点击的脚本
                print("[错误] " + hint.replace("\n\n", "\n"), file=sys.stderr)
            sys.exit(1)
        self.base_size = self.idle_pm.size()
        # 素材宽度 / 显示基准宽度 = 超采样倍数；显示尺寸按基准归一化，
        # 于是换成高清素材只会更清楚，不会把桌宠放大。
        self.asset_oversample = max(
            1.0, self.base_size.width() / float(self.DESIGN_WIDTH))
        self.design_size = QSize(
            max(1, round(self.base_size.width() / self.asset_oversample)),
            max(1, round(self.base_size.height() / self.asset_oversample)))

        # 状态
        self.state = "idle"
        self.frame_idx = 0
        self.state_start = time.monotonic()
        self.last_tick = time.monotonic()
        self.current_pm = None      # 首个 tick 生成，避免按素材原始尺寸误画一帧
        self.dragging = False
        self.drag_offset = QPoint()
        self.press_pos = QPoint()
        self.pressed = False
        self.render_dy = 0.0
        self._float_gain = 1.0
        self._mask_key = None
        # 渲染缓存：同一帧 + 同一目标尺寸只缩放一次（旧代码每 tick 重缩放，白耗 CPU）
        self._pm_cache = {}
        self.idle_img = self.idle_pm.toImage()
        # 眨眼帧：由 tools/make_blink.py 生成，与 idle 同画布同坐标系；
        # 目录不存在就退化成不眨眼，不会报错
        self.blink_imgs = []
        bdir = ASSET_DIR / "blink"
        if bdir.is_dir():
            for f in sorted(bdir.glob("*.webp")):
                im = QImage(str(f))
                if not im.isNull() and im.size() == self.base_size:
                    self.blink_imgs.append(im)
        self._blink_seq = []
        self._blink_pos = 0
        self._blink_repeat = False
        self.next_blink = time.monotonic() + random.uniform(*self.BLINK_GAP_S)

        # 说话帧：由 tools/make_talk.py 从待机帧生成（下颌位移），同画布
        self.talk_imgs = []
        tdir = ASSET_DIR / "talk"
        if tdir.is_dir():
            for f in sorted(tdir.glob("*.webp")):
                im = QImage(str(f))
                if not im.isNull() and im.size() == self.base_size:
                    self.talk_imgs.append(im)
        # 语音：音频 + 每帧张嘴量曲线（tools/make_voice.py 生成）
        self.voice_lines = []
        self.voice_effects = []
        curves = ASSET_DIR / "voice" / "curves.json"
        if curves.exists() and self.talk_imgs:
            try:
                data = json.loads(curves.read_text(encoding="utf-8"))
            except Exception as exc:
                log(f"[警告] 语音曲线读取失败: {exc}")
                data = {"lines": []}
            for ln in data.get("lines", []):
                p = ASSET_DIR / ln.get("file", "")
                if p.exists() and ln.get("open"):
                    self.voice_lines.append({**ln, "path": p})
        self.voice_idx = -1
        self.voice_start = 0.0

        self.scale = float(self.settings.value("scale", 1.0, type=float))
        self.muted = self.settings.value("muted", "false") == "true"
        # 静止状态：不上下浮动。不是"暂停当前动作"，而是一个独立的常驻状态，
        # 右键勾上就一直不浮动（眨眼、点击大笑照常）。
        self.static = self.settings.value("static", "false") == "true"

        # 定时器：统一驱动待机浮动与动作帧
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(self.FPS_MS)

        # 音效：QSoundEffect 低延迟播放 wav；多媒体模块不可用时静默降级
        self.sound = None
        try:
            from PySide6.QtMultimedia import QSoundEffect
            wav = ASSET_DIR / "laugh.wav"
            if wav.exists():
                self.sound = QSoundEffect(self)
                self.sound.setSource(QUrl.fromLocalFile(str(wav)))
                self.sound.setVolume(0.9)
                self.sound.setMuted(self.muted)
        except Exception as exc:
            log(f"[警告] 音效模块不可用，将无声运行: {exc}")

        # 语音音效：每句一个 QSoundEffect 预加载（避免第一次点击才加载的延迟）
        if self.voice_lines:
            try:
                from PySide6.QtMultimedia import QSoundEffect
                for ln in self.voice_lines:
                    eff = QSoundEffect(self)
                    eff.setSource(QUrl.fromLocalFile(str(ln["path"])))
                    eff.setVolume(0.95)
                    eff.setMuted(self.muted)
                    self.voice_effects.append(eff)
            except Exception as exc:
                log(f"[警告] 语音模块不可用，只做口型不发声: {exc}")
                self.voice_effects = []

        # 打包成 --windowed 后没有控制台，Qt 的多媒体初始化信息看不到。
        # 设 PET_SOUND_REPORT=<路径> 就让它在启动 1.2 秒后写一份音频状态，
        # 用来确认"打包版到底能不能出声"（平时不设变量就完全不执行）。
        if os.environ.get("PET_SOUND_REPORT"):
            QTimer.singleShot(1200, self._dump_sound_status)

        self.apply_scale(self.scale, place=not self._restore_position())
        self.build_menu()
        self.build_tray()

    # ------------------------------------------------------------ 尺寸/外观

    def scaled_base(self) -> QSize:
        b = self.body_logical()
        return QSize(b.width(), b.height() + 2 * self.FLOAT_MARGIN)

    # ------------------------------------------------------------ 尺寸换算

    def body_logical(self) -> QSize:
        """当前档位的身体绘制尺寸（逻辑像素，不含浮动余量）"""
        return QSize(int(self.design_size.width() * self.scale),
                     int(self.design_size.height() * self.scale))

    def dpr(self) -> float:
        return max(1.0, float(self.devicePixelRatioF()))

    def body_device(self) -> QSize:
        """身体绘制尺寸（物理像素）：素材直接缩放到这里，避免二次重采样"""
        b = self.body_logical()
        d = self.dpr()
        return QSize(round(b.width() * d), round(b.height() * d))

    def apply_scale(self, scale: float, place: bool = False):
        self.scale = scale
        self._pm_cache.clear()
        self._mask_key = None
        self.resize(self.scaled_base())
        self.settings.setValue("scale", scale)
        if place:
            self.move_to_bottom_right()

    def move_to_bottom_right(self):
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.right() - self.width() - 24
        y = geo.bottom() - self.height() - 12
        self.move(x, y)

    def _restore_position(self) -> bool:
        x = self.settings.value("x", None)
        y = self.settings.value("y", None)
        if x is None or y is None:
            return False
        self.move(int(x), int(y))
        return True

    # ------------------------------------------------------------ 菜单/托盘

    def build_menu(self):
        self.menu = QMenu(self)
        self.menu.setStyleSheet(
            "QMenu{font-size:13px;}"
            "QMenu:item{padding:5px 18px;}"
        )

        size_menu = self.menu.addMenu("大小")
        self.size_actions = {}
        ag = QActionGroup(self.menu)
        for name, value in SCALE_LEVELS.items():
            act = QAction(name, self.menu, checkable=True)
            act.setChecked(abs(value - self.scale) < 0.01)
            act.triggered.connect(lambda _=False, v=value: self.apply_scale(v))
            ag.addAction(act)
            size_menu.addAction(act)
            self.size_actions[name] = act

        self.act_static = QAction("静止（不上下浮动）", self.menu, checkable=True)
        self.act_static.setChecked(self.static)
        self.act_static.triggered.connect(self.toggle_static)
        self.menu.addAction(self.act_static)

        # 音频：点一句就让桌宠边说边动嘴（口型跟着音频能量走）
        if self.voice_lines:
            voice_menu = self.menu.addMenu("音频")
            self.voice_actions = []
            for i, ln in enumerate(self.voice_lines):
                act = QAction(ln["label"], self.menu)
                act.triggered.connect(lambda _=False, k=i: self.play_voice(k))
                voice_menu.addAction(act)
                self.voice_actions.append(act)
        else:
            self.voice_actions = []

        self.act_mute = QAction("静音", self.menu, checkable=True)
        self.act_mute.setChecked(self.muted)
        self.act_mute.triggered.connect(self.toggle_mute)
        self.menu.addAction(self.act_mute)

        if IS_WIN:
            self.act_autostart = QAction("开机自启", self.menu, checkable=True)
            self.act_autostart.setChecked(self.autostart_enabled())
            self.act_autostart.triggered.connect(self.toggle_autostart)
            self.menu.addAction(self.act_autostart)

        self.menu.addSeparator()
        act_hide = QAction("隐藏桌宠（Alt+Shift+H）", self.menu)
        act_hide.triggered.connect(self.hide)
        self.menu.addAction(act_hide)

        act_back = QAction("回到右下角", self.menu)
        act_back.triggered.connect(self.move_to_bottom_right)
        self.menu.addAction(act_back)

        self.menu.addSeparator()
        act_quit = QAction("退出", self.menu)
        act_quit.triggered.connect(self.quit_app)
        self.menu.addAction(act_quit)

    def build_tray(self):
        self.tray = QSystemTrayIcon(self.make_tray_icon(), self)
        self.tray.setToolTip("桌宠")
        menu = QMenu(self)
        act_show = QAction("显示/隐藏桌宠", self)
        act_show.triggered.connect(self.toggle_visible)
        menu.addAction(act_show)
        if IS_WIN:
            act_hotkey = QAction("快捷键 Alt+Shift+H 隐藏", self)
            act_hotkey.setEnabled(False)
            menu.addAction(act_hotkey)
        menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible()
            if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def make_tray_icon(self) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#F5C02E"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(6, 10, 52, 48)
        p.setBrush(QColor("#3E3E3E"))
        p.drawEllipse(22, 24, 6, 6)
        p.drawEllipse(36, 24, 6, 6)
        p.setBrush(QColor("#F3E3C6"))
        p.drawEllipse(20, 36, 24, 16)
        p.end()
        return QIcon(pm)

    # ------------------------------------------------------------ 交互

    def play_laugh(self):
        # 大笑时把正在播的语音收掉，避免两个声音叠在一起
        if self.voice_idx >= 0:
            self.stop_voice()
        self.state = "laugh"
        self.frame_idx = 0
        self.state_start = time.monotonic()
        if self.sound is not None:
            self.sound.stop()
            if not self.muted:
                self.sound.play()

    def toggle_static(self):
        """静止：只停自主浮动，眨眼与点击大笑照常。"""
        self.static = self.act_static.isChecked()
        self.settings.setValue("static", "true" if self.static else "false")

    def toggle_mute(self):
        self.muted = self.act_mute.isChecked()
        self.settings.setValue("muted", "true" if self.muted else "false")
        if self.sound is not None:
            self.sound.setMuted(self.muted)
            if self.muted:
                self.sound.stop()
        for eff in self.voice_effects:
            eff.setMuted(self.muted)
            if self.muted:
                eff.stop()

    # ------------------------------------------------------------ 语音/说话

    def play_voice(self, idx: int):
        """播一句语音，同时把口型切到"说话"状态。"""
        if not (0 <= idx < len(self.voice_lines)):
            return
        if self.state == "laugh":          # 正在大笑就先收掉，免得两个动作打架
            self.state = "idle"
            self.frame_idx = 0
            if self.sound is not None:
                self.sound.stop()
        self.stop_voice(keep_idx=True)
        self.voice_idx = idx
        self.voice_start = time.monotonic()
        if 0 <= idx < len(self.voice_effects) and not self.muted:
            eff = self.voice_effects[idx]
            eff.stop()
            eff.play()

    def stop_voice(self, keep_idx: bool = False):
        for eff in self.voice_effects:
            eff.stop()
        if not keep_idx:
            self.voice_idx = -1

    def _dump_sound_status(self):
        """把音频后端状态写到 PET_SOUND_REPORT 指定的文件（仅诊断用）。"""
        try:
            lines = []
            if self.sound is None:
                lines.append("laugh: 未创建 QSoundEffect")
            else:
                lines.append("laugh: status=%s" % self.sound.status().name)
            lines.append("voice_count=%d effects=%d"
                         % (len(self.voice_lines), len(self.voice_effects)))
            for i, eff in enumerate(self.voice_effects):
                lines.append("voice%d: status=%s" % (i, eff.status().name))
            lines.append("muted=%s" % self.muted)
            from pathlib import Path as _P
            _P(os.environ["PET_SOUND_REPORT"]).write_text(
                "\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def voice_frame(self, now):
        """返回当前该用的说话帧索引；不在说话时返回 -1。"""
        if self.voice_idx < 0 or not self.talk_imgs:
            return -1
        ln = self.voice_lines[self.voice_idx]
        el = now - self.voice_start
        curve = ln.get("open") or []
        if el > float(ln.get("duration", 0)) + 0.20 or not curve:
            self.voice_idx = -1
            # 说话期间没在跑眨眼调度，结束后别让它立刻补眨一下
            self.next_blink = max(self.next_blink, now + 1.0)
            return -1
        idx = int(el * float(ln.get("fps", 30)))
        openv = float(curve[max(0, min(idx, len(curve) - 1))])
        if openv <= 0.10:
            return -1                      # 气口：闭嘴
        n = len(self.talk_imgs)
        return max(0, min(n - 1, int(round(openv * (n - 1)))))

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.raise_()

    def quit_app(self):
        self.settings.setValue("x", self.x())
        self.settings.setValue("y", self.y())
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    # ------------------------------------------------------------ 鼠标事件

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.pressed = True
            self.press_pos = e.globalPosition().toPoint()
            self.dragging = False
            self.drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if not self.pressed:
            return
        if not self.dragging and (
            (e.globalPosition().toPoint() - self.press_pos).manhattanLength() > 4
        ):
            self.dragging = True
        if self.dragging:
            self.move(e.globalPosition().toPoint() - self.drag_offset)

    def moveEvent(self, e):
        # 拖动后实时记忆位置，避免异常关机丢失
        self.settings.setValue("x", self.x())
        self.settings.setValue("y", self.y())
        super().moveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.pressed:
            was_dragging = self.dragging
            self.pressed = False
            self.dragging = False
            if not was_dragging:
                # 单击（没有拖动）-> 大笑；播放中再次点击 -> 重播
                self.play_laugh()

    def contextMenuEvent(self, e):
        self.menu.exec(e.globalPos())

    # ------------------------------------------------------------ 渲染

    def tick(self):
        now = time.monotonic()
        self.last_tick = now

        # 待机：连续正弦浮动，全程浮点。旧写法 int() 把 5 秒周期切成 5 级台阶，
        # 两端各停 1.3 秒、中间“啪”地跳过，这就是看起来一顿一顿的原因。
        dy = self.IDLE_FLOAT_PX * math.sin(now * 2 * math.pi / self.FLOAT_PERIOD)
        # 静止状态与大笑期间都让增益渐变到 0；直接归零会“弹”掉最多一个振幅。
        # 静止靠增益而不是直接 dy=0，取消静止时也是渐入，不会突然开始飘。
        if self.static or self.state == "laugh":
            self._float_gain = max(0.0, self._float_gain - 0.25)
        else:
            self._float_gain = min(1.0, self._float_gain + 0.08)
        dy *= self._float_gain
        self.render_dy = dy

        mask_src = None
        if self.state == "laugh":
            elapsed = now - self.state_start
            idx = int(elapsed * self.FPS)
            if idx >= self.frame_count:
                self.state = "idle"
                self.frame_idx = 0
                self.state_start = now
                if self.sound is not None:
                    self.sound.stop()
            else:
                self.frame_idx = idx
                img = self._decode_frame(idx)
                self.current_pm = QPixmap.fromImage(self._scaled_to(img, self.body_device()))
                # 遮罩坐标是设备无关像素，必须用逻辑尺寸的那份
                mask_src = QPixmap.fromImage(self._scaled_to(img, self.body_logical()))
                self._mask_key = ("laugh", idx, self.body_logical().width())
        if self.state == "idle":
            vf = self.voice_frame(now)
            if vf >= 0:
                # 说话中：用张嘴帧替换当前帧。_idle_source 在这时不调用，
                # 所以眨眼序列不会推进，不会出现"边说边眨"的打架。
                src, tag = self.talk_imgs[vf], "v%d" % vf
            else:
                src, tag = self._idle_source(now)
            self.current_pm = self._pm_cached(src, "d" + tag, self.body_device())
            # 眨眼/说话帧都只动五官、不动轮廓，遮罩继续用 idle 那份，
            # 于是待机期间一次 SetWindowRgn 都不用发
            mask_src = self._idle_at_logical()
            self._mask_key = ("idle", self.body_logical().width())

        # 透明区鼠标穿透：只在轮廓真的变了时才重算并下发 SetWindowRgn。
        # 待机轮廓不变，于是 30Hz 的窗口区域重建直接省掉（那也是抖动来源之一）。
        if self._mask_key != getattr(self, "_applied_mask_key", None):
            region = QRegion(mask_src.mask())
            region.translate(0, self.FLOAT_MARGIN)
            self.setMask(region)
            self._applied_mask_key = self._mask_key
        self.update()

    def _decode_frame(self, idx):
        """序列帧解码小缓存：定时器抖动导致同一帧被取两次时不必重复解 webp"""
        cache = getattr(self, "_frame_cache", None)
        if cache is None:
            cache = self._frame_cache = {}
        img = cache.get(idx)
        if img is None:
            img = cache[idx] = QImage(str(self.frame_paths[idx]))
            if len(cache) > 4:
                for k in sorted(cache)[: len(cache) - 4]:
                    if k != idx:
                        del cache[k]
        return img

    @staticmethod
    def _scaled_to(img, size):
        if img.size() == size:
            return img
        return img.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    # ------------------------------------------------------------ 待机 / 眨眼

    def _pm_cached(self, src, tag, size):
        """同一（帧, 目标尺寸）只缩放一次；待机与眨眼帧都不多，缓存命中率很高"""
        key = (tag, size.width(), size.height())
        pm = self._pm_cache.get(key)
        if pm is None:
            pm = QPixmap.fromImage(self._scaled_to(src, size))
            if len(self._pm_cache) > 32:
                self._pm_cache.clear()
            self._pm_cache[key] = pm
        return pm

    def _blink_sequence(self):
        n = len(self.blink_imgs)
        if n < 2:
            return []
        return list(range(n)) + list(range(n - 2, -1, -1))

    def _idle_source(self, now):
        """返回 (该显示的图像, 缓存标签)。眨眼进度在这里推进。"""
        if self.blink_imgs:
            if self._blink_seq:
                idx = self._blink_seq[self._blink_pos]
                self._blink_pos += 1
                if self._blink_pos >= len(self._blink_seq):
                    if self._blink_repeat:
                        self._blink_repeat = False
                        self._blink_seq = self._blink_sequence()
                        self._blink_pos = 0
                    else:
                        self._blink_seq = []
                        self._blink_pos = 0
                return self.blink_imgs[idx], "b%d" % idx
            if now >= self.next_blink:
                self._blink_repeat = random.random() < self.BLINK_DOUBLE_P
                self._blink_seq = self._blink_sequence()
                self._blink_pos = 0
                self.next_blink = now + random.uniform(*self.BLINK_GAP_S)
        return self.idle_img, ""

    def _idle_at_logical(self):
        return self._pm_cached(self.idle_img, "l", self.body_logical())

    def paintEvent(self, _e):
        pm = self.current_pm
        if pm is None or pm.isNull():
            return
        d = self.dpr()
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        # 目标矩形按物理像素取整，素材与屏幕 1:1 对齐，不再被系统缩放二次重采样
        target = QRectF(0.0, self.FLOAT_MARGIN + self.render_dy,
                        pm.width() / d, pm.height() / d)
        p.drawPixmap(target, pm, QRectF(0.0, 0.0, pm.width(), pm.height()))
        p.end()

    # ------------------------------------------------------------ Windows 热键

    def showEvent(self, e):
        super().showEvent(e)
        if IS_WIN and not getattr(self, "_hotkey_registered", False):
            self._register_hotkey()
            self._hotkey_registered = True

    def _register_hotkey(self):
        try:
            import ctypes
            self._user32 = ctypes.windll.user32
            self._hotkey_id = 1
            self._user32.RegisterHotKey(
                int(self.winId()), self._hotkey_id,
                MOD_ALT | MOD_SHIFT, VK_H,
            )
        except Exception:
            pass

    def nativeEvent(self, eventType, message):
        if IS_WIN and eventType == "windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    self.toggle_visible()
                    return True, 0
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    # ------------------------------------------------------------ 开机自启

    def _autostart_key(self):
        return QSettings(
            r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run",
            QSettings.NativeFormat,
        )

    def autostart_enabled(self) -> bool:
        if not IS_WIN:
            return False
        return self._autostart_key().value("nailong-pet") is not None

    def toggle_autostart(self):
        key = self._autostart_key()
        if self.act_autostart.isChecked():
            if getattr(sys, "frozen", False):
                target = f'"{sys.executable}"'
            else:
                # 源码运行时用 pythonw.exe，避免开机弹出黑色控制台
                pythonw = sys.executable.replace("python.exe", "pythonw.exe")
                target = f'"{pythonw}" "{Path(__file__).resolve()}"'
            key.setValue("nailong-pet", target)
        else:
            key.remove("nailong-pet")


# ---------------------------------------------------------------- 入口

def main():
    # Windows 下高 DPI 显示清晰
    if IS_WIN:
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("nailong-pet")
    app.setQuitOnLastWindowClosed(False)  # 关闭窗口不退出（托盘常驻）

    # 单实例：朋友双击两次不该冒出两只桌宠。用 QLockFile，进程崩了锁会自动失效
    lock = QLockFile(QDir.temp().filePath("nailong-pet.lock"))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(150):
        log("已有一只桌宠在运行，本次退出")
        sys.exit(0)

    pet = PetWindow()
    pet.show()

    # 保留全局引用，防止被回收（lock 也要留住，否则析构时会把锁释放掉）
    app._pet = pet
    app._lock = lock
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
