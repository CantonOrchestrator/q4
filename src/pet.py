#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面宠物 Desktop Pet —— DeepSeek 对话 + 离线语音 + 状态记忆 + 快捷键
======================================================================
纯本地运行 UI；仅通过 HTTPS 调用 DeepSeek Chat API 获取对话回复。

依赖：
    pip install PyQt5 pyttsx3
    可选（推荐，用于全局热键）：pip install pynput
    Linux 语音引擎：sudo apt install espeak-ng libespeak1

配置 API Key：
    Windows PowerShell: $env:DEEPSEEK_API_KEY = "sk-xxxx"
    Linux / macOS     : export DEEPSEEK_API_KEY="sk-xxxx"

快捷键：
    F9   —— 切换宠物配色（红→橙→黄→绿→蓝→靛→紫 循环，不影响图片/GIF）
    F10  —— 全屏截图并复制到剪贴板

操作：
    · 左键拖动       移动宠物（不会误触点击）
    · 左键单击       随机动作 + 气泡互动
    · 左键双击       打开聊天窗口
    · 右键菜单       聊天 / 静音 / 走动 / 更换图片 / 恢复默认 / 退出

状态记忆文件：~/.desktop_pet_state.json
"""

import os
import sys
import json
import math
import html as html_mod
import random
import queue
import threading
import time
import datetime
import urllib.error
import urllib.request

from PyQt5.QtCore import (
    Qt, QTimer, QPointF, QRectF, QThread, pyqtSignal,
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QBrush, QPainterPath, QFont,
    QFontMetrics, QRadialGradient, QCursor, QKeySequence,
    QPixmap, QMovie,
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QMenu, QShortcut, QTextBrowser,
    QFileDialog,
)


# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
SYSTEM_PROMPT = (
    "你是名叫“小蓝”的桌面宠物，是一只圆滚滚小生物。"
    "你说话亲切、简短、俏皮，每次回复不超过 60 个字，可以偶尔使用颜文字。"
    "当用户问技术问题时，用通俗易懂的话回答。"
    "不要透露你是 AI 模型，保持宠物人设。"
)


# 配色主题（顺序按“红橙黄绿蓝靛紫”，默认蓝=索引4）
COLOR_THEMES = [
    {"name": "红",
     "body": "#ff9a9a", "edge": "#e05555", "tail": "#ff8080",
     "grad_in": "#fff0f0", "grad_mid": "#ffc0c0", "grad_out": "#ff9a9a"},
    {"name": "橙",
     "body": "#ffb877", "edge": "#e89442", "tail": "#ffa050",
     "grad_in": "#fff5e8", "grad_mid": "#ffd9b0", "grad_out": "#ffb877"},
    {"name": "黄",
     "body": "#ffe27a", "edge": "#e0c030", "tail": "#ffd040",
     "grad_in": "#fffbe8", "grad_mid": "#ffeaa0", "grad_out": "#ffe27a"},
    {"name": "绿",
     "body": "#9ee09e", "edge": "#4cbf4c", "tail": "#7ed67e",
     "grad_in": "#f0fff0", "grad_mid": "#c0f0c0", "grad_out": "#9ee09e"},
    {"name": "蓝",
     "body": "#8fd3ff", "edge": "#5bb6ea", "tail": "#79c4f2",
     "grad_in": "#eaf9ff", "grad_mid": "#b6e4ff", "grad_out": "#8fd3ff"},
    {"name": "靛",
     "body": "#9ab0ff", "edge": "#5566ee", "tail": "#7d92ff",
     "grad_in": "#eef0ff", "grad_mid": "#bcc8ff", "grad_out": "#9ab0ff"},
    {"name": "紫",
     "body": "#c9a8ff", "edge": "#9e63ee", "tail": "#b890ff",
     "grad_in": "#f6eeff", "grad_mid": "#ddc4ff", "grad_out": "#c9a8ff"},
]
DEFAULT_THEME_INDEX = 4   # 蓝


# --------------------------------------------------------------------------- #
# 状态持久化
# --------------------------------------------------------------------------- #
def get_state_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".desktop_pet_state.json")


def load_state() -> dict:
    p = get_state_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(data: dict) -> None:
    try:
        with open(get_state_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def greeting_for_now() -> str:
    """根据本机时间给出问候语。"""
    h = datetime.datetime.now().hour
    if 5 <= h < 11:
        return "早上好"
    if 11 <= h < 13:
        return "中午好"
    if 13 <= h < 18:
        return "下午好"
    if 18 <= h < 23:
        return "晚上好"
    return "夜深了"


# --------------------------------------------------------------------------- #
# 本地语音朗读（独立线程，绝不阻塞 UI）
# --------------------------------------------------------------------------- #
class Speaker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tasks = queue.Queue()
        self.engine = None
        self.enabled = True

    def run(self):
        try:
            import pyttsx3
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 175)
            self.engine.setProperty("volume", 1.0)
            try:
                for v in self.engine.getProperty("voices"):
                    tag = f"{getattr(v, 'name', '')} {getattr(v, 'id', '')}".lower()
                    if "chinese" in tag or "zh" in tag or "huihui" in tag:
                        self.engine.setProperty("voice", v.id)
                        break
            except Exception:
                pass
        except Exception:
            self.engine = None

        while True:
            text = self.tasks.get()
            if text is None:
                break
            if not self.enabled or not self.engine or not text:
                continue
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception:
                pass

    def say(self, text: str):
        self.tasks.put(text)

    def stop(self):
        self.tasks.put(None)


# --------------------------------------------------------------------------- #
# DeepSeek Chat API
# --------------------------------------------------------------------------- #
class DeepSeekWorker(QThread):
    reply_ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, messages, parent=None):
        super().__init__(parent)
        self.messages = messages

    def run(self):
        key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            self.failed.emit(
                "未检测到环境变量 DEEPSEEK_API_KEY。\n"
                "请先设置它，然后重新启动程序。"
            )
            return

        payload = {
            "model": "deepseek-chat",
            "messages": self.messages,
            "temperature": 1.0,
            "max_tokens": 512,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            obj = json.loads(raw)
            text = (obj["choices"][0]["message"]["content"] or "").strip()
            self.reply_ready.emit(text or "（没有收到内容）")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            self.failed.emit(f"请求失败（HTTP {e.code}）\n{body}")
        except urllib.error.URLError as e:
            self.failed.emit(f"网络错误：{e.reason}")
        except Exception as e:
            self.failed.emit(f"出错了：{e}")


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def text_width(fm: QFontMetrics, s: str) -> int:
    if hasattr(fm, "horizontalAdvance"):
        return fm.horizontalAdvance(s)
    return fm.width(s)


# --------------------------------------------------------------------------- #
# 气泡
# --------------------------------------------------------------------------- #
class SpeechBubble(QWidget):
    def __init__(self):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            | Qt.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.font = QFont("Microsoft YaHei", 10)
        self.max_width = 260
        self.lines = [""]
        self._pet = None

        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

        self.follow_timer = QTimer(self)
        self.follow_timer.setInterval(60)
        self.follow_timer.timeout.connect(lambda: self.follow(self._pet))

    def _layout_text(self, text: str):
        fm = QFontMetrics(self.font)
        limit = self.max_width - 28
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if cur and text_width(fm, cur + ch) > limit:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        self.lines = lines or [""]
        w = max([text_width(fm, l) for l in self.lines] + [50]) + 28
        h = len(self.lines) * fm.height() + 24
        self.resize(int(w), int(h + 12))

    def show_for_pet(self, pet, text: str, duration: int = 4000):
        self._pet = pet
        self._layout_text(text)
        self.follow(pet)
        self.show()
        self.raise_()
        self.hide_timer.start(duration)
        self.follow_timer.start()

    def follow(self, pet):
        if pet is None or not self.isVisible():
            return
        geo = pet.frameGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.top() - self.height() + 14
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        if y < screen.top() + 4:
            y = geo.bottom() - 14
        self.move(int(x), int(y))

    def hideEvent(self, e):
        self.follow_timer.stop()
        super().hideEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height() - 12
        if h <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, w - 2, h - 2), 13, 13)
        tri = QPainterPath()
        tri.moveTo(w / 2 - 9, h - 2)
        tri.lineTo(w / 2, h + 10)
        tri.lineTo(w / 2 + 9, h - 2)
        tri.closeSubpath()
        path = path.united(tri)

        p.setPen(QPen(QColor("#8fd3ff"), 2.2))
        p.setBrush(QColor(255, 255, 255, 244))
        p.drawPath(path)

        p.setPen(QColor("#33475a"))
        p.setFont(self.font)
        fm = QFontMetrics(self.font)
        y = 13 + fm.ascent()
        for line in self.lines:
            p.drawText(14, y, line)
            y += fm.height()


# --------------------------------------------------------------------------- #
# 对话窗口
# --------------------------------------------------------------------------- #
class ChatDialog(QWidget):
    submitted = pyqtSignal(str)
    closed = pyqtSignal()

    STYLE = """
        QFrame#box { background: rgba(255, 255, 255, 250);
                     border: 2px solid #8fd3ff; border-radius: 18px; }
        QLabel#title { color: #4a6572; font-size: 13px; font-weight: bold; }
        QTextBrowser { background: #f8fcff; border: 1px solid #d9ecff;
                       border-radius: 10px; padding: 6px;
                       font-size: 13px; color: #2b3a45; }
        QLineEdit { background: #f0f8ff; border: 1px solid #cfe6ff;
                    border-radius: 10px; padding: 7px 10px;
                    font-size: 14px; color: #2b3a45; }
        QLineEdit:focus { border: 1px solid #8fd3ff; }
        QLineEdit:disabled { background: #eef2f5; color: #9aa7b0; }
        QPushButton { background: #8fd3ff; border: none; border-radius: 10px;
                      padding: 7px 16px; color: #0b2a3d;
                      font-size: 13px; font-weight: bold; }
        QPushButton:hover  { background: #a9dfff; }
        QPushButton:pressed{ background: #79c4f2; }
        QPushButton:disabled { background: #cfe6f5; color: #8aa0b0; }
    """

    def __init__(self, pet_name="小蓝"):
        super().__init__(
            None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(420, 380)

        box = QFrame(self)
        box.setObjectName("box")
        box.setGeometry(8, 8, 404, 364)
        box.setStyleSheet(self.STYLE)

        title = QLabel(f"和 {pet_name} 聊天  （Esc 关闭）", box)
        title.setObjectName("title")

        self.view = QTextBrowser(box)
        self.view.setOpenExternalLinks(False)

        self.edit = QLineEdit(box)
        self.edit.setPlaceholderText("问我点什么吧……（回车发送）")

        self.btn = QPushButton("发送", box)

        lay = QVBoxLayout(box)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(title)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn)
        lay.addLayout(row)

        self.btn.clicked.connect(self._submit)
        self.edit.returnPressed.connect(self._submit)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def _submit(self):
        text = self.edit.text().strip()
        if not text:
            return
        self.edit.clear()
        self.submitted.emit(text)

    def append_message(self, who: str, text: str, color: str = "#3a7fb5"):
        safe = html_mod.escape(text).replace("\n", "<br>")
        self.view.append(
            f'<p style="margin:6px 0; line-height:1.4;">'
            f'<b style="color:{color};">{html_mod.escape(who)}：</b>{safe}</p>'
        )
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_busy(self, busy: bool):
        self.edit.setEnabled(not busy)
        self.btn.setEnabled(not busy)
        if busy:
            self.edit.setPlaceholderText("小蓝正在思考中……")
        else:
            self.edit.setPlaceholderText("问我点什么吧……（回车发送）")
            self.edit.setFocus()

    def move_next_to(self, pet):
        geo = pet.frameGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        x = geo.center().x() - self.width() // 2
        y = geo.top() - self.height() + 24
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        if y < screen.top() + 8:
            y = geo.bottom() - 24
        y = min(y, screen.bottom() - self.height() - 8)
        self.move(int(x), int(y))

    def closeEvent(self, e):
        self.closed.emit()
        super().closeEvent(e)


# --------------------------------------------------------------------------- #
# 桌面宠物
# --------------------------------------------------------------------------- #
class DesktopPet(QWidget):
    W = 160
    H = 160
    FPS = 33

    # 供全局热键 / QShortcut 触发的信号（避免跨线程直接操作 UI）
    _sig_color = pyqtSignal()
    _sig_screenshot = pyqtSignal()

    CLICK_LINES = [
        "嘿嘿～", "挠痒痒吗？", "我在呢！", "嗯？", "你今天好可爱！",
        "摸摸头～", "要不要休息一下？", "我有点困了……",
        "双击我就能聊天啦！", "按 F9 可以换颜色哦～",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("桌面宠物 - 小蓝")
        self.resize(self.W, self.H)

        # 位置（先放默认值，稍后若状态文件存在会被覆盖）
        screen = QApplication.primaryScreen().availableGeometry()
        self.pet_x = float(screen.right() - 240)
        self.pet_y = float(screen.bottom() - 210)
        self.move(int(self.pet_x), int(self.pet_y))

        # 组件
        self.speaker = Speaker()
        self.speaker.start()
        self.bubble = SpeechBubble()
        self.dialog = None
        self._worker = None
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]

        # 形象
        self.movie = None
        self.pixmap = None
        self.image_path = None
        self._try_load_default_assets()

        # 配色
        self.theme_index = DEFAULT_THEME_INDEX

        # 状态
        self.phase = 0.0
        self.direction = -1
        self.state = "idle"
        self.state_timer = 60
        self.speed = 1.0
        self.auto_walk = True
        self.dragging = False
        self.moved = False
        self.drag_offset = None
        self.press_global = None
        self._suppress_click = False

        self.eye_dx = 0.0
        self.eye_dy = 0.0
        self.blinking = False
        self.blink_frames = 0
        self.blink_countdown = random.randint(60, 150)

        self.jump_offset = 0.0
        self.jump_velocity = 0.0

        # 快捷键去重时间
        self._last_color_time = 0.0
        self._last_shot_time = 0.0
        self._hotkey_listener = None
        self._kb = None

        # ------- 载入上次保存的状态 -------
        self._load_state()

        # 单击 / 双击区分计时器
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)

        # 主循环
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000 // self.FPS)

        # ------- 快捷键：F9 换色 / F10 截图 -------
        QShortcut(QKeySequence("F9"), self,
                  activated=self._on_color_triggered)
        QShortcut(QKeySequence("F10"), self,
                  activated=self._on_screenshot_triggered)
        self._sig_color.connect(self._on_color_triggered)
        self._sig_screenshot.connect(self._on_screenshot_triggered)
        self._start_global_hotkeys()

    # ================================================================== #
    # 状态记忆
    # ================================================================== #
    def _persist_state(self):
        save_state({
            "pet_x": float(self.pet_x),
            "pet_y": float(self.pet_y),
            "voice_enabled": bool(self.speaker.enabled),
            "auto_walk": bool(self.auto_walk),
            "theme_index": int(self.theme_index),
            "image_path": self.image_path or "",
        })

    def _load_state(self):
        state = load_state()
        if not state:
            return

        # 位置
        if "pet_x" in state and "pet_y" in state:
            try:
                x = float(state["pet_x"])
                y = float(state["pet_y"])
                g = QApplication.primaryScreen().availableGeometry()
                x = max(g.left(), min(x, g.right() - self.width() + 1))
                y = max(g.top(), min(y, g.bottom() - self.height() + 1))
                self.pet_x, self.pet_y = x, y
                self.move(int(x), int(y))
            except Exception:
                pass

        # 语音 / 走动
        if "voice_enabled" in state:
            self.speaker.enabled = bool(state["voice_enabled"])
        if "auto_walk" in state:
            self.auto_walk = bool(state["auto_walk"])

        # 配色
        if "theme_index" in state:
            try:
                idx = int(state["theme_index"])
                if 0 <= idx < len(COLOR_THEMES):
                    self.theme_index = idx
            except Exception:
                pass

        # 图片
        img = state.get("image_path") or ""
        if img and os.path.exists(img):
            self.load_pet_image(img)

    # ================================================================== #
    # 全局热键（pynput 可选）
    # ================================================================== #
    def _start_global_hotkeys(self):
        try:
            from pynput import keyboard
        except Exception:
            return
        self._kb = keyboard
        try:
            listener = keyboard.Listener(on_press=self._on_pynput_press)
            listener.daemon = True
            listener.start()
            self._hotkey_listener = listener
        except Exception:
            self._hotkey_listener = None

    def _on_pynput_press(self, key):
        try:
            kb = self._kb
            if key == kb.Key.f9:
                self._sig_color.emit()
            elif key == kb.Key.f10:
                self._sig_screenshot.emit()
        except Exception:
            pass
        return True

    # ================================================================== #
    # 快捷键处理（去重）
    # ================================================================== #
    def _on_color_triggered(self):
        now = time.monotonic()
        if now - self._last_color_time < 0.2:
            return
        self._last_color_time = now
        self.cycle_color()

    def _on_screenshot_triggered(self):
        now = time.monotonic()
        if now - self._last_shot_time < 0.2:
            return
        self._last_shot_time = now
        self.take_screenshot()

    def cycle_color(self):
        """默认形象换色：红→橙→黄→绿→蓝→靛→紫 循环。图片/GIF 不受影响。"""
        self.theme_index = (self.theme_index + 1) % len(COLOR_THEMES)
        self.update()
        theme_name = COLOR_THEMES[self.theme_index]["name"]
        if self.movie is not None or self.pixmap is not None:
            self.say(f"换到 {theme_name} 色啦（不过图片形象不变哦）", speak=False)
        else:
            self.say(f"换到 {theme_name} 色啦～", speak=False)
        self._persist_state()

    def take_screenshot(self):
        """F10：全屏截图并复制到剪贴板。"""
        try:
            screen = QApplication.primaryScreen()
            pix = screen.grabWindow(0)
            if pix is None or pix.isNull():
                self.say("截图失败了……", speak=False)
                return
            QApplication.clipboard().setPixmap(pix)
            self.say(f"截图已复制到剪贴板！({pix.width()}×{pix.height()})",
                     speak=False)
        except Exception as e:
            self.say(f"截图出错：{e}", speak=False)

    # ================================================================== #
    # 形象加载
    # ================================================================== #
    def _try_load_default_assets(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for name in ("pet.gif", "pet.webp", "pet.png", "pet.jpg", "pet.jpeg"):
            p = os.path.join(base, "assets", name)
            if os.path.exists(p):
                if self.load_pet_image(p):
                    return

    def load_pet_image(self, path: str) -> bool:
        if not path or not os.path.exists(path):
            return False
        if self.movie is not None:
            try:
                self.movie.stop()
                self.movie.frameChanged.disconnect(self.update)
            except Exception:
                pass
            self.movie = None

        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            movie = QMovie(path)
            if not movie.isValid():
                return False
            movie.frameChanged.connect(self.update)
            movie.start()
            self.movie = movie
            self.pixmap = None
            self.image_path = path
            self.update()
            return True

        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.pixmap = pix
        self.movie = None
        self.image_path = path
        self.update()
        return True

    def reset_default_look(self):
        if self.movie is not None:
            try:
                self.movie.stop()
            except Exception:
                pass
        self.movie = None
        self.pixmap = None
        self.image_path = None
        self.update()
        self._persist_state()

    # ================================================================== #
    # 动画主循环
    # ================================================================== #
    def tick(self):
        self.phase += 0.16

        if self.jump_velocity != 0 or self.jump_offset < 0:
            self.jump_offset += self.jump_velocity
            self.jump_velocity += 1.1
            if self.jump_offset >= 0:
                self.jump_offset = 0
                self.jump_velocity = 0

        # 眨眼 + 眼睛跟随（仅默认绘制形象）
        if self.movie is None and self.pixmap is None:
            if self.blinking:
                self.blink_frames -= 1
                if self.blink_frames <= 0:
                    self.blinking = False
                    self.blink_countdown = random.randint(60, 150)
            else:
                self.blink_countdown -= 1
                if self.blink_countdown <= 0:
                    self.blinking = True
                    self.blink_frames = 4

            local = self.mapFromGlobal(QCursor.pos())
            cx = self.width() / 2.0
            cy = self.height() / 2.0 + 6
            dx = local.x() - cx
            dy = local.y() - cy
            dist = math.hypot(dx, dy)
            if dist > 1:
                k = min(1.0, dist / 220.0)
                self.eye_dx = dx / dist * 3.5 * k
                self.eye_dy = dy / dist * 3.5 * k
            else:
                self.eye_dx = self.eye_dy = 0.0

        # 自动走动
        dialog_open = self.dialog is not None and self.dialog.isVisible()
        if self.auto_walk and not self.dragging and not dialog_open:
            self.state_timer -= 1
            if self.state == "walk":
                screen = QApplication.primaryScreen().availableGeometry()
                left = screen.left()
                right = screen.right() - self.width() + 1
                self.pet_x += self.direction * self.speed
                if self.pet_x <= left:
                    self.pet_x = left
                    self.direction = 1
                elif self.pet_x >= right:
                    self.pet_x = right
                    self.direction = -1
                self.move(int(self.pet_x), int(self.pet_y))
            if self.state_timer <= 0:
                self._switch_state()

        self.update()

    def _switch_state(self):
        if self.state == "walk":
            self.state = "idle"
            self.state_timer = random.randint(50, 160)
        else:
            self.state = "walk"
            self.direction = random.choice([-1, 1])
            self.state_timer = random.randint(60, 220)

    # ================================================================== #
    # 绘制
    # ================================================================== #
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        w, h = self.width(), self.height()

        pix = None
        if self.movie is not None:
            pix = self.movie.currentPixmap()
        if (pix is None or pix.isNull()) and self.pixmap is not None:
            pix = self.pixmap

        if pix is not None and not pix.isNull():
            bob = math.sin(self.phase) * 2.0 + self.jump_offset
            pw, ph = pix.width(), pix.height()
            scale = min(self.W / max(pw, 1), self.H / max(ph, 1))
            dw, dh = pw * scale, ph * scale
            p.save()
            p.translate(w / 2.0, h / 2.0 + bob)
            p.scale(self.direction, 1)
            p.drawPixmap(QRectF(-dw / 2.0, -dh / 2.0, dw, dh), pix,
                         QRectF(pix.rect()))
            p.restore()
            return

        self._draw_default_pet(p)

    def _draw_default_pet(self, p: QPainter):
        theme = COLOR_THEMES[self.theme_index]
        body_color = QColor(theme["body"])
        edge_color = QColor(theme["edge"])
        tail_color = QColor(theme["tail"])
        grad_in = QColor(theme["grad_in"])
        grad_mid = QColor(theme["grad_mid"])
        grad_out = QColor(theme["grad_out"])

        w, h = self.width(), self.height()
        bob = math.sin(self.phase) * 2.6
        if self.state == "walk" and not self.dragging and self.auto_walk:
            bob -= abs(math.sin(self.phase * 2.4)) * 2.0
        bob += self.jump_offset

        cx = w / 2.0
        cy = h / 2.0 + 6 + bob

        # 影子
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(QPointF(cx, h - 16), 38, 8)

        p.save()
        p.translate(cx, 0)
        p.scale(self.direction, 1)
        p.translate(-cx, 0)

        pen = QPen(edge_color, 2.2)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)

        # 尾巴
        sway = math.sin(self.phase * 1.5) * 8
        tail = QPainterPath()
        tail.moveTo(cx + 30, cy + 22)
        tail.quadTo(cx + 64, cy + 26 + sway * 0.4,
                    cx + 58 + sway, cy - 18 - sway * 0.3)
        p.setPen(QPen(tail_color, 9, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(tail)

        # 耳朵
        p.setPen(pen)
        p.setBrush(body_color)
        p.drawEllipse(QPointF(cx - 26, cy - 33), 15, 15)
        p.drawEllipse(QPointF(cx + 26, cy - 33), 15, 15)

        # 身体
        grad = QRadialGradient(cx - 14, cy - 18, 72)
        grad.setColorAt(0.0, grad_in)
        grad.setColorAt(0.55, grad_mid)
        grad.setColorAt(1.0, grad_out)
        p.setBrush(QBrush(grad))
        p.setPen(pen)
        p.drawEllipse(QPointF(cx, cy), 42, 42)

        # 眼睛
        # 修复：p.scale(self.direction, 1) 会水平翻转绘制坐标系，
        # 当 direction = -1 时，眼睛的水平偏移需反向补偿，
        # 否则眼珠会朝鼠标反方向看。
        eye_y = cy - 4
        eye_dx_draw = self.eye_dx * self.direction
        if self.blinking:
            p.setPen(QPen(QColor("#2b3a45"), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx - 21, eye_y), QPointF(cx - 9, eye_y))
            p.drawLine(QPointF(cx + 9, eye_y), QPointF(cx + 21, eye_y))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2b3a45"))
            for ex in (cx - 15, cx + 15):
                p.drawEllipse(QPointF(ex + eye_dx_draw, eye_y + self.eye_dy),
                              7.0, 8.5)
            p.setBrush(QColor(255, 255, 255, 235))
            for ex in (cx - 15, cx + 15):
                # 高光相对眼睛的偏移也要一起补偿，保证高光始终在左上方
                p.drawEllipse(
                    QPointF(ex + eye_dx_draw - 2.4 * self.direction,
                            eye_y + self.eye_dy - 3.2),
                    2.4, 2.6
                )     

        # 嘴
        p.setPen(QPen(QColor("#3a4a55"), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - 8, cy + 8, 16, 11), 180 * 16, 180 * 16)

        # 腮红
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 150, 170, 95))
        p.drawEllipse(QPointF(cx - 31, cy + 9), 8, 5)
        p.drawEllipse(QPointF(cx + 31, cy + 9), 8, 5)

        p.restore()

    # ================================================================== #
    # 鼠标交互
    # ================================================================== #
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_click = False
            self.dragging = True
            self.moved = False
            self.press_global = event.globalPos()
            self.drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.dragging and self.drag_offset is not None:
            if (event.globalPos() - self.press_global).manhattanLength() > 5:
                self.moved = True
            self.move(event.globalPos() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.dragging:
            self.dragging = False
            if self.moved:
                self._snap_to_screen()
                self.pet_x = float(self.x())
                self.pet_y = float(self.y())
                self._persist_state()
            else:
                if not self._suppress_click:
                    self._click_timer.start(230)
            self._suppress_click = False
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._suppress_click = True
            self.dragging = False
            self.open_chat()
            event.accept()

    def _on_single_click(self):
        self.jump_velocity = -5.5
        self.say(random.choice(self.CLICK_LINES), speak=False)

    def _snap_to_screen(self):
        g = QApplication.primaryScreen().availableGeometry()
        x = max(g.left(), min(self.x(), g.right() - self.width() + 1))
        y = max(g.top(), min(self.y(), g.bottom() - self.height() + 1))
        self.move(int(x), int(y))

    # ================================================================== #
    # 右键菜单
    # ================================================================== #
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #ffffff; border: 2px solid #8fd3ff;
                    border-radius: 10px; padding: 6px; }
            QMenu::item { padding: 6px 24px 6px 16px; border-radius: 6px;
                          color: #2b3a45; font-size: 13px; }
            QMenu::item:selected { background: #e3f3ff; }
            QMenu::separator { height: 1px; background: #e3f3ff;
                               margin: 4px 8px; }
        """)

        act_chat = menu.addAction("💬  和我聊天（也可双击）")
        act_voice = menu.addAction(
            "🔇  静音（关闭语音）" if self.speaker.enabled else "🔊  开启语音朗读"
        )
        act_walk = menu.addAction(
            "🚶  暂停自动走动" if self.auto_walk else "🚶  开启自动走动"
        )
        menu.addSeparator()
        act_shot = menu.addAction("📷  截图到剪贴板（F10）")
        act_color = menu.addAction("🎨  换个颜色（F9）")
        menu.addSeparator()
        act_img = menu.addAction("🖼  更换宠物图片…")
        act_reset = menu.addAction("🔄  恢复默认形象")
        menu.addSeparator()
        act_quit = menu.addAction("❌  退出")

        chosen = menu.exec_(event.globalPos())

        if chosen == act_chat:
            self.open_chat()
        elif chosen == act_voice:
            self.speaker.enabled = not self.speaker.enabled
            self.say("语音已开启" if self.speaker.enabled
                     else "语音已关闭，安静陪你～", speak=False)
            self._persist_state()
        elif chosen == act_walk:
            self.auto_walk = not self.auto_walk
            if not self.auto_walk:
                self.state = "idle"
            self.say("我去散步啦～" if self.auto_walk
                     else "我就在这里陪着你～", speak=False)
            self._persist_state()
        elif chosen == act_shot:
            self._on_screenshot_triggered()
        elif chosen == act_color:
            self._on_color_triggered()
        elif chosen == act_img:
            self._choose_image()
        elif chosen == act_reset:
            self.reset_default_look()
            self.say("还是原来的样子舒服～", speak=False)
        elif chosen == act_quit:
            self.quit_app()

    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择宠物图片", "",
            "图片文件 (*.gif *.webp *.png *.jpg *.jpeg *.bmp)"
        )
        if not path:
            return
        if self.load_pet_image(path):
            self.say("新形象不错吧？", speak=True)
            self._persist_state()
        else:
            self.say("这张图片我打不开呢……", speak=False)

    # ================================================================== #
    # 对话
    # ================================================================== #
    def open_chat(self):
        if self.dialog is not None and self.dialog.isVisible():
            self.dialog.raise_()
            self.dialog.activateWindow()
            self.dialog.edit.setFocus()
            return

        self.dialog = ChatDialog("小蓝")
        self.dialog.submitted.connect(self._on_user_submit)
        self.dialog.closed.connect(self._on_dialog_closed)

        for m in self.history[1:]:
            who = "你" if m["role"] == "user" else "小蓝"
            self.dialog.append_message(who, m["content"])

        if self._worker is not None and self._worker.isRunning():
            self.dialog.set_busy(True)

        self.dialog.move_next_to(self)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
        self.dialog.edit.setFocus()

    def _on_dialog_closed(self):
        self.dialog = None
        self.state_timer = 30

    def _on_user_submit(self, text: str):
        if self._worker is not None and self._worker.isRunning():
            return
        self.history.append({"role": "user", "content": text})
        if self.dialog is not None:
            self.dialog.append_message("你", text)
            self.dialog.set_busy(True)
        self.say("让我想想……", speak=False)

        msgs = [self.history[0]] + self.history[-10:]
        worker = DeepSeekWorker(msgs)
        worker.reply_ready.connect(self._on_api_reply)
        worker.failed.connect(self._on_api_failed)
        worker.finished.connect(lambda w=worker: self._cleanup_worker(w))
        self._worker = worker
        worker.start()

    def _cleanup_worker(self, w):
        if self._worker is w:
            self._worker = None

    def _on_api_reply(self, text: str):
        self.history.append({"role": "assistant", "content": text})
        if len(self.history) > 12:
            self.history = [self.history[0]] + self.history[-10:]
        if self.dialog is not None:
            self.dialog.append_message("小蓝", text)
            self.dialog.set_busy(False)
        self.say(text, speak=True)

    def _on_api_failed(self, err: str):
        if self.dialog is not None:
            self.dialog.append_message("系统", err, color="#c0392b")
            self.dialog.set_busy(False)
        self.say("哎呀，我好像出了点问题……", speak=False)

    # ================================================================== #
    # 说话
    # ================================================================== #
    def say(self, text: str, speak: bool = True):
        self.bubble.show_for_pet(self, text)
        if speak and self.speaker.enabled:
            clean = "".join(
                ch for ch in text
                if ord(ch) < 0x1F000 and ch not in "♪♫💙⏰🎵"
            ).strip()
            if clean:
                self.speaker.say(clean)

    # ================================================================== #
    # 退出
    # ================================================================== #
    def quit_app(self):
        self._persist_state()
        self.timer.stop()
        self._click_timer.stop()
        if self._worker is not None and self._worker.isRunning():
            self._worker.wait(2000)
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
        if self.dialog is not None:
            self.dialog.close()
        self.bubble.close()
        self.speaker.stop()
        QApplication.quit()

    def closeEvent(self, event):
        self._persist_state()
        self.timer.stop()
        self.speaker.stop()
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("DesktopPet")
    app.setQuitOnLastWindowClosed(False)

    pet = DesktopPet()
    pet.show()

    # 时间段问候
    greeting = greeting_for_now()
    QTimer.singleShot(500, lambda: pet.say(
        f"{greeting}！我是小蓝，双击我可以聊天～按 F9 换色，F10 截图",
        speak=True,
    ))

    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        QTimer.singleShot(3000, lambda: pet.say(
            "提示：请先设置环境变量 DEEPSEEK_API_KEY 再启动，我才能和你聊天～",
            speak=False,
        ))

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()