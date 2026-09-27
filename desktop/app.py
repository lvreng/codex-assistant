#!/usr/bin/env python3
"""Native desktop window around the production ESP32 LVGL framebuffer."""
from __future__ import annotations

import argparse
from collections import deque
import fcntl
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from PySide6.QtCore import QDate, QDateTime, QEvent, QSettings, QSize, Qt, QTimer, QRectF
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QMessageBox, QSizePolicy, QStyle,
    QSystemTrayIcon, QToolBar, QToolButton, QWidget, QWidgetAction,
    QCheckBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
)

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "host"))
from desktop_ipc import DesktopClient, runtime_dir
from renderer import LIBRARY, Renderer
from celestial import ARTWORK, MODELS, CelestialLayer, model_art
from celestial_motion import AstraOptions
from astra_controls import AstraControls

NATIVE_THEMES = (
    ("dark", "深色", "#071019", "#e9f6fb"),
    ("light", "浅色", "#f4f7f8", "#162630"),
    ("beach", "海滩玻璃", "#dff5e8", "#192126"),
    ("pixel", "像素游戏", "#09091d", "#f7f7ff"),
    ("mechanical", "机械", "#e3eaed", "#19252f"),
    ("paper", "折纸", "#fff1e7", "#202c43"),
    ("glass", "水光玻璃", "#dff6f3", "#122e39"),
    ("vangogh", "梵高星空", "#07162d", "#f1d57a"),
)
AUTO_THEME = 8
AUTO_PALETTE = ("auto", "思想者", "#071019", "#e9f6fb")
# Firmware IDs must stay stable; retired desktop entries are not renumbered.
THEMES = {index: value for index, value in enumerate(NATIVE_THEMES) if index >= 2}
THEMES[AUTO_THEME] = AUTO_PALETTE
THEME_ORDER = tuple(THEMES)


def saved_theme(value):
    return value if value in THEMES else AUTO_THEME


class Instance:
    def __init__(self):
        self.directory = runtime_dir()
        self.lock = (self.directory / "desktop.lock").open("a")
        os.chmod(self.directory / "desktop.lock", 0o600)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.path = self.directory / "window.sock"
        self.primary = False
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            try:
                self.socket.sendto(b"SHOW", str(self.path))
            except OSError:
                pass
            return
        self.primary = True
        self.path.unlink(missing_ok=True)
        self.socket.bind(str(self.path))
        os.chmod(self.path, 0o600)

    def requested(self):
        try:
            return self.socket.recv(16) == b"SHOW"
        except BlockingIOError:
            return False

    def close(self):
        self.socket.close()
        if self.primary:
            self.path.unlink(missing_ok=True)
        self.lock.close()


class Canvas(QWidget):
    def __init__(self, renderer):
        super().__init__()
        self.renderer = renderer
        self.image = QImage(renderer.buffer, 800, 480, 1600, QImage.Format.Format_RGB16)
        self.setMinimumSize(480, 288)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pressed = False
        self.background = QColor("#071019")
        self.celestial = CelestialLayer()
        self.adaptive = False
        self.motion_preview = False
        self.paint_count = 0
        self.paint_samples = deque(maxlen=240)

    def sizeHint(self):
        return QSize(800, 480)

    def viewport(self):
        scale = min(self.width() / 800, self.height() / 480)
        width, height = 800 * scale, 480 * scale
        return QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height)

    def paintEvent(self, _event):
        started = time.perf_counter()
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.background)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.renderer.value(1) != 3)
        viewport = self.viewport()
        painter.drawImage(viewport, self.image)
        if self.adaptive and self.celestial.current:
            now = time.monotonic()
            state = self.renderer.value(2)
            connected = bool(self.renderer.value(0))
            if self.motion_preview:
                # A preview remains useful while the bridge is offline, but it
                # never changes the telemetry values rendered by the native UI.
                state = state if state in (1, 2, 4, 8) else 1
                connected = True
            self.celestial.set_state(state, connected, now)
            painter.save()
            painter.translate(viewport.topLeft())
            painter.scale(viewport.width() / 800, viewport.height() / 480)
            self.celestial.paint(painter, self.renderer.value(9), now)
            painter.restore()
        brightness = self.renderer.value(6)
        if brightness < 86:
            painter.fillRect(viewport, QColor(0, 0, 0, int((1 - brightness / 86) * 255)))
        painter.end()
        self.paint_count += 1
        self.paint_samples.append((time.perf_counter() - started) * 1000)

    def point(self, position):
        viewport = self.viewport()
        return (
            int((position.x() - viewport.x()) * 800 / viewport.width()),
            int((position.y() - viewport.y()) * 480 / viewport.height()),
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.viewport().contains(event.position()):
            self.pressed = True
            self.setFocus()
            self.renderer.pointer(*self.point(event.position()), True)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.pressed:
            self.renderer.pointer(*self.point(event.position()), True)

    def mouseReleaseEvent(self, event):
        if self.pressed and event.button() == Qt.MouseButton.LeftButton:
            self.renderer.pointer(*self.point(event.position()), False)
            self.pressed = False

    def focusOutEvent(self, event):
        if self.pressed:
            self.renderer.pointer(0, 0, False)
            self.pressed = False
        super().focusOutEvent(event)


class MotionPreviewMenu(QMenu):
    """Keep model preview actions open so the artwork can be inspected live."""

    def _preview_action(self, action):
        return action if action and action.data() else None

    def mouseReleaseEvent(self, event):
        action = self._preview_action(self.activeAction())
        if action:
            action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            action = self._preview_action(self.activeAction())
            if action:
                action.trigger()
                event.accept()
                return
        super().keyPressEvent(event)


class MembershipExpiryDialog(QDialog):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("会员到期时间")
        self.setMinimumWidth(340)
        layout = QFormLayout(self)
        self.enabled = QCheckBox("已设置", self)
        self.enabled.setChecked(bool(value))
        self.date_time = QDateTimeEdit(self)
        self.date_time.setCalendarPopup(True)
        self.date_time.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.date_time.setDateRange(QDate(2000, 1, 1), QDate(2199, 12, 31))
        self.date_time.setDateTime(QDateTime.fromString(value, "yyyy-MM-ddTHH:mm")
                                   if value else QDateTime.currentDateTime())
        self.date_time.setEnabled(bool(value))
        self.enabled.toggled.connect(self.date_time.setEnabled)
        layout.addRow("会员到期", self.enabled)
        layout.addRow("本地时间", self.date_time)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def value(self):
        return self.date_time.dateTime().toString("yyyy-MM-ddTHH:mm") if self.enabled.isChecked() else ""


class Window(QMainWindow):
    def __init__(self, instance=None, settings=None, client=None):
        super().__init__()
        self.instance = instance
        self.settings = settings or QSettings("CodexAssistant", "Desktop")
        self.client = client or DesktopClient()
        self.renderer = Renderer()
        self.canvas = Canvas(self.renderer)
        self.setCentralWidget(self.canvas)
        self.setWindowTitle("Codex助手")
        icon_path = PROJECT / "desktop/icon.png"
        self.setWindowIcon(QIcon(str(icon_path)) if icon_path.exists() else QIcon.fromTheme("face-smile"))
        self.setMinimumSize(640, 418)
        self.resize(800, 514)
        self.last_theme = -1
        self.last_generation = -1
        self.last_tick_ns = time.monotonic_ns()
        self.last_diagnostic = 0.0
        self.frames = 0
        self.max_frame_ms = 0.0
        self.tray = None
        self.closed = False
        self.motion_preview_active = False
        self.preview_model = None
        self.model_test_pending_until = 0.0
        self.model_test_pending_state = None
        self.model_test_ui_key = None
        self.model_test_request_error = ""
        self.make_toolbar()
        self.restore()
        # Decode before showing the window, not in the three-second model rotation.
        scale = min(self.width() / 800, (self.height() - 34) / 480)
        self.canvas.celestial.preload(scale * self.devicePixelRatioF())
        self.make_tray()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.pump)
        self.timer.start(16)

    def icon(self, name, fallback):
        return QIcon.fromTheme(name, self.style().standardIcon(fallback))

    def action(self, label, name, fallback, callback, shortcut=None, checkable=False):
        result = QAction(self.icon(name, fallback), label, self)
        result.setToolTip(label)
        result.setCheckable(checkable)
        result.triggered.connect(callback)
        if shortcut:
            result.setShortcut(shortcut)
        self.addAction(result)
        return result

    def make_toolbar(self):
        self.toolbar = QToolBar()
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setFixedHeight(34)
        self.addToolBar(self.toolbar)
        style = QStyle.StandardPixmap
        self.previous = self.action("上一对话", "go-previous", style.SP_ArrowLeft,
                                    lambda: self.client.navigate(-1), "Left")
        self.next = self.action("下一对话", "go-next", style.SP_ArrowRight,
                                    lambda: self.client.navigate(1), "Right")
        self.toolbar.addAction(self.previous)
        self.toolbar.addAction(self.next)
        self.model_test_action = QAction(self.icon("media-playback-start", style.SP_MediaPlay),
                                          "ESP32 四模型切换测试", self)
        self.model_test_action.setToolTip("开始 ESP32 四模型切换测试")
        self.model_test_action.setEnabled(False)
        self.model_test_action.triggered.connect(self.toggle_model_test)
        self.addAction(self.model_test_action)
        self.model_test_button = QToolButton()
        self.model_test_button.setDefaultAction(self.model_test_action)
        self.model_test_button.setAccessibleName("ESP32 四模型切换测试")
        self.toolbar.addWidget(self.model_test_button)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)
        self.motion_menu = MotionPreviewMenu("动效预览", self)
        self.motion_menu.setObjectName("motionPreviewMenu")
        self.motion_actions = {}
        self.api_menu = self.motion_menu.addMenu("API 来源")
        self.api_group = QActionGroup(self)
        self.api_actions = {}
        for mode, label in (("auto", "自动识别"), ("official", "官方 OpenAI"), ("morecode", "MoreCode")):
            action = self.api_menu.addAction(label)
            action.setCheckable(True)
            self.api_group.addAction(action)
            action.triggered.connect(lambda _checked=False, m=mode: self.client.set_api_mode(m))
            self.api_actions[mode] = action
        self.api_actions["auto"].setChecked(True)
        self.api_menu.addSeparator()
        self.expiry_action = self.api_menu.addAction("会员到期时间...")
        self.expiry_action.triggered.connect(self.edit_membership_expiry)
        self.rotation_menu = self.motion_menu.addMenu("对话滚动")
        self.rotation_group = QActionGroup(self)
        self.rotation_group.setExclusive(True)
        self.rotation_actions = {}
        for mode, label in (("off", "关闭滚动"), ("active", "只滚动执行任务"),
                            ("all", "滚动全部")):
            action = self.rotation_menu.addAction(label)
            action.setCheckable(True)
            self.rotation_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, m=mode: self.client.set_rotation_mode(m))
            self.rotation_actions[mode] = action
        self.rotation_actions["active"].setChecked(True)
        self.motion_menu.addSeparator()
        for key, (_slug, name, body, accent) in MODELS.items():
            slug = MODELS[key][0]
            artwork = ARTWORK / f"{slug}.png"
            icon = QIcon(str(artwork)) if artwork.exists() else QIcon()
            action = self.motion_menu.addAction(icon, f"{name}  ·  {body.title()}")
            action.setData(key)
            action.setCheckable(True)
            action.hovered.connect(lambda k=key: self.preview_motion(k))
            action.triggered.connect(lambda _checked=False, k=key: self.preview_motion(k))
            self.motion_actions[key] = action
        self.motion_menu.addSection("Astra 群星")
        self.astra_controls = AstraControls(self.motion_menu)
        controls_action = QWidgetAction(self.motion_menu)
        controls_action.setDefaultWidget(self.astra_controls)
        self.motion_menu.addAction(controls_action)
        self.astra_controls.styleChanged.connect(self.set_astra_style)
        self.astra_controls.periodsChanged.connect(self.set_astra_periods)
        self.astra_controls.resetRequested.connect(self.reset_astra_periods)
        self.motion_menu.addSeparator()
        self.motion_menu.addAction(self.model_test_action)
        self.motion_model_test_action = self.model_test_action
        self.motion_menu.addSeparator()
        restore_action = self.motion_menu.addAction("关闭预览并恢复思想者")
        restore_action.triggered.connect(self.restore_adaptive_preview)
        self.motion_menu.aboutToShow.connect(self.begin_motion_preview)
        self.motion_menu.aboutToHide.connect(self.end_motion_preview)
        self.motion_button = QToolButton()
        self.motion_button.setIcon(self.icon("view-media-playlist", style.SP_FileDialogListView))
        self.motion_button.setToolTip("动效预览")
        self.motion_button.setAccessibleName("动效预览")
        self.motion_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.motion_button.setMenu(self.motion_menu)
        self.toolbar.addWidget(self.motion_button)
        self.theme_menu = QMenu("主题", self)
        self.theme_group = QActionGroup(self)
        self.theme_actions = {}
        for index, (_slug, label, bg, _fg) in THEMES.items():
            swatch = QPixmap(16, 16)
            swatch.fill(QColor(bg))
            action = self.theme_menu.addAction(QIcon(swatch), label)
            action.setCheckable(True)
            self.theme_group.addAction(action)
            action.triggered.connect(lambda _checked=False, i=index: self.select_theme(i))
            self.theme_actions[index] = action
        self.theme_button = QToolButton()
        self.theme_button.setIcon(self.icon("preferences-desktop-theme", style.SP_DesktopIcon))
        self.theme_button.setToolTip("主题")
        self.theme_button.setAccessibleName("主题")
        self.theme_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.theme_button.setMenu(self.theme_menu)
        self.toolbar.addWidget(self.theme_button)
        self.action("切换主题", "view-refresh", style.SP_BrowserReload, self.cycle_theme, "T")
        self.details_action = self.action(
            "今日统计", "office-chart-line", style.SP_FileDialogDetailedView,
            self.toggle_details, "D", True)
        self.toolbar.addAction(self.details_action)
        self.pin_action = self.action(
            "窗口置顶", "view-pin", style.SP_TitleBarShadeButton, self.set_pinned,
            "Ctrl+P", True)
        self.toolbar.addAction(self.pin_action)
        self.scale_menu = QMenu("窗口尺寸", self)
        for percent in (80, 100, 125, 150):
            action = self.scale_menu.addAction(f"{percent}%")
            action.triggered.connect(lambda _checked=False, p=percent: self.set_scale(p))
        self.scale_button = QToolButton()
        self.scale_button.setIcon(self.icon("zoom-in", style.SP_TitleBarMaxButton))
        self.scale_button.setToolTip("窗口尺寸")
        self.scale_button.setAccessibleName("窗口尺寸")
        self.scale_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.scale_button.setMenu(self.scale_menu)
        self.toolbar.addWidget(self.scale_button)
        self.action("返回", "go-previous", style.SP_ArrowBack,
                    lambda: self.renderer.details(False), "Escape")
        self.action("退出", "application-exit", style.SP_DialogCloseButton, self.close, "Ctrl+Q")
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self.context_menu)

    def context_menu(self, position):
        menu = QMenu(self)
        menu.addAction(self.previous)
        menu.addAction(self.next)
        menu.addSeparator()
        menu.addMenu(self.theme_menu)
        menu.addAction(self.details_action)
        menu.addAction(self.pin_action)
        menu.addMenu(self.scale_menu)
        menu.exec(self.canvas.mapToGlobal(position))

    def make_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Codex助手")
        menu = QMenu(self)
        menu.addAction("显示窗口", self.present)
        menu.addMenu(self.theme_menu)
        menu.addAction(self.pin_action)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.present() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    def restore(self):
        options = self.read_astra_options()
        self.astra_controls.set_options(options)
        self.canvas.celestial.set_astra_options(options, animate=False)
        self.select_theme(saved_theme(self.settings.value("theme", AUTO_THEME, int)))
        pinned = self.settings.value("pinned", False, bool)
        self.pin_action.setChecked(pinned)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        # A disconnected external monitor must not strand the popup offscreen.
        if not any(screen.availableGeometry().intersects(self.frameGeometry())
                   for screen in QApplication.screens()):
            area = QApplication.primaryScreen().availableGeometry()
            self.move(area.center() - self.rect().center())

    def read_astra_options(self, style=None):
        style = AstraOptions.validated(
            self.settings.value("astra/style", "linked") if style is None else style).style
        return AstraOptions.validated(
            style, self.settings.value(f"astra/{style}/minimum"),
            self.settings.value(f"astra/{style}/maximum"))

    def apply_astra_options(self, options):
        options = AstraOptions.validated(options.style, options.minimum, options.maximum)
        self.settings.setValue("astra/style", options.style)
        self.settings.setValue(f"astra/{options.style}/minimum", options.minimum)
        self.settings.setValue(f"astra/{options.style}/maximum", options.maximum)
        self.settings.sync()
        self.astra_controls.set_options(options)
        self.canvas.celestial.set_astra_options(options)
        if self.motion_preview_active:
            self.preview_motion("astra")
        self.canvas.update()

    def set_astra_style(self, style):
        self.apply_astra_options(self.read_astra_options(style))

    def set_astra_periods(self, minimum, maximum):
        self.apply_astra_options(AstraOptions(
            self.canvas.celestial.astra_options.style, minimum, maximum))

    def reset_astra_periods(self):
        self.apply_astra_options(AstraOptions.validated(self.canvas.celestial.astra_options.style))

    def select_theme(self, theme):
        theme = saved_theme(theme)
        self.settings.setValue("theme", theme)
        self.selected_theme = theme
        self.canvas.adaptive = theme == AUTO_THEME
        self.apply_model_theme()
        self.last_theme = -1

    def apply_model_theme(self):
        if self.motion_preview_active and self.preview_model:
            self.canvas.adaptive = True
            self.canvas.motion_preview = True
            self.canvas.celestial.select(self.preview_model)
            self.renderer.theme(0)
            return
        if self.selected_theme == AUTO_THEME:
            key = model_art(getattr(self.client, "model", ""))
            if key:
                key = self.canvas.celestial.select(key)
            else:
                self.canvas.celestial.select(None)
            # Unknown models use a retained character theme, never a guessed body.
            self.renderer.theme(0 if key else 6)
        else:
            self.renderer.theme(self.selected_theme)

    def cycle_theme(self):
        index = THEME_ORDER.index(self.selected_theme)
        self.select_theme(THEME_ORDER[(index + 1) % len(THEME_ORDER)])

    def begin_motion_preview(self):
        if self.motion_preview_active:
            return
        self.motion_preview_active = True
        # Preview is intentionally temporary. Opening it always enters the
        # adaptive palette so the celestial artwork has a stable presentation.
        self.select_theme(AUTO_THEME)
        current = model_art(getattr(self.client, "model", ""))
        self.preview_model = current or self.canvas.celestial.current or "sol"
        self.preview_motion(self.preview_model)

    def preview_motion(self, key):
        if not self.motion_preview_active or key not in MODELS:
            return
        self.preview_model = key
        self.canvas.adaptive = True
        self.canvas.motion_preview = True
        self.canvas.celestial.select(key)
        self.renderer.theme(0)
        for model, action in self.motion_actions.items():
            action.setChecked(model == key)
        self.last_theme = -1
        self.canvas.update()

    def restore_adaptive_preview(self):
        self.motion_menu.hide()

    def end_motion_preview(self):
        if not self.motion_preview_active:
            return
        self.motion_preview_active = False
        self.preview_model = None
        self.canvas.motion_preview = False
        self.select_theme(AUTO_THEME)
        self.canvas.update()

    def toggle_details(self, _checked=False):
        self.renderer.details(not self.renderer.value(5))

    def toggle_model_test(self, _checked=False):
        status = getattr(self.client, "model_test_status", {}) or {}
        active = bool(status.get("active"))
        if time.monotonic() < self.model_test_pending_until:
            return
        request = getattr(self.client, "request_model_test", None)
        if not callable(request):
            return
        if request(not active):
            self.model_test_pending_until = time.monotonic() + 2.0
            self.model_test_pending_state = not active
            self.model_test_request_error = ""
        else:
            self.model_test_request_error = "上位机未连接"
        self.update_model_test_ui()

    def update_model_test_ui(self, now=None):
        now = time.monotonic() if now is None else now
        status = getattr(self.client, "model_test_status", {}) or {}
        updated = float(getattr(self.client, "model_test_updated", 0) or 0)
        fresh = updated > 0 and now - updated <= 4.0
        available = bool(status.get("available")) and fresh
        active = bool(status.get("active")) and fresh
        if self.model_test_pending_state is not None:
            if fresh and (active == self.model_test_pending_state or status.get("error")):
                self.model_test_pending_until = 0
                self.model_test_pending_state = None
            elif now >= self.model_test_pending_until:
                self.model_test_pending_state = None
                self.model_test_request_error = "设备未确认测试"
        pending = now < self.model_test_pending_until
        step = int(status.get("step", 0) or 0)
        model = int(status.get("model", 0) or 0)
        names = ("", "Sol", "Terra", "Luna", "Astra")
        if active and 1 <= step <= 4:
            label = f"ESP32 测试 {step}/4 {names[model] if 0 <= model < len(names) else ''}".rstrip()
            tooltip = f"停止 ESP32 四模型切换测试（{step}/4）"
        else:
            label = "ESP32 四模型切换测试"
            tooltip = "开始 ESP32 四模型切换测试"
        error = self.model_test_request_error or str(status.get("error") or "")
        error = {"no_sd": "SD 卡未就绪", "disconnected": "ESP32 未连接",
                 "unsupported": "固件不支持测试"}.get(error, error)
        if error:
            tooltip = f"{tooltip}；失败：{error}"
        elif not available:
            tooltip = "ESP32 未连接或固件不支持测试"
        key = (label, tooltip, active, available and not pending)
        if key == self.model_test_ui_key:
            return
        self.model_test_ui_key = key
        icon = self.icon("media-playback-stop" if active else "media-playback-start",
                         QStyle.StandardPixmap.SP_MediaStop if active else QStyle.StandardPixmap.SP_MediaPlay)
        self.model_test_action.setText(label)
        self.model_test_action.setIcon(icon)
        self.model_test_action.setToolTip(tooltip)
        self.model_test_action.setEnabled(available and not pending)
        self.model_test_button.setAccessibleName(label)

    def edit_membership_expiry(self):
        dialog = MembershipExpiryDialog(self.renderer.account_text(6), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not self.client.set_membership_expiry(dialog.value()):
                QMessageBox.warning(self, "会员到期时间", "上位机未连接，设置未保存。")

    def set_pinned(self, enabled):
        self.settings.setValue("pinned", bool(enabled))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(enabled))
        self.show()

    def set_scale(self, percent):
        self.showNormal()
        size = QSize(800 * percent // 100, 480 * percent // 100 + self.toolbar.height())
        available = self.screen().availableGeometry().size() - QSize(24, 64)
        self.resize(size.boundedTo(available))

    def present(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def pump(self):
        started = time.monotonic_ns()
        elapsed_ms = max(0, (started - self.last_tick_ns) // 1_000_000)
        self.last_tick_ns += elapsed_ms * 1_000_000
        now = started / 1_000_000_000
        self.client.subscribe(now)
        packets = self.client.receive()
        for packet in packets:
            self.renderer.feed(packet)
        if packets:
            self.apply_model_theme()
            mode = self.renderer.value(10)
            if 0 <= mode < 3:
                self.api_actions[("auto", "official", "morecode")[mode]].setChecked(True)
        rotation_mode = getattr(self.client, "rotation_mode", "active")
        if rotation_mode in self.rotation_actions:
            self.rotation_actions[rotation_mode].setChecked(True)
        self.update_model_test_ui(now)
        generation = self.renderer.tick(elapsed_ms)
        delta = self.renderer.navigate()
        for _ in range(min(abs(delta), 8)):
            self.client.navigate(1 if delta > 0 else -1)
        if generation != self.last_generation or self.canvas.adaptive:
            self.canvas.update()
            self.last_generation = generation
            self.frames += 1
        theme = self.selected_theme
        adaptive_fallback = theme == AUTO_THEME and not self.canvas.celestial.current
        palette_key = (theme, adaptive_fallback)
        if palette_key != self.last_theme:
            self.last_theme = palette_key
            _slug, _label, bg, fg = NATIVE_THEMES[6] if adaptive_fallback else THEMES[theme]
            self.canvas.background = QColor(bg)
            hover = "255, 255, 255" if theme in (3, 7, AUTO_THEME) and not adaptive_fallback else "22, 38, 48"
            self.setStyleSheet(
                f"QToolBar {{ background: {bg}; border: 0; border-bottom: 1px solid rgba({hover}, 30); }}"
                f"QToolButton {{ color: {fg}; border: 0; border-radius: 4px; padding: 4px 7px; }}"
                f"QToolButton:hover, QToolButton:checked {{ background: rgba({hover}, 20); }}"
                f"QMenu {{ background: {bg}; color: {fg}; padding: 4px; border: 1px solid {fg}; }}"
                f"QMenu::item {{ padding: 6px 24px; }}"
                f"QMenu::item:selected {{ background: rgba({hover}, 30); }}"
                f"QMenu#motionPreviewMenu::item {{ padding: 8px 28px 8px 8px; min-width: 210px; }}"
                f"QWidget#astraControls {{ background: transparent; color: {fg}; }}"
                f"QWidget#astraControls QLabel {{ color: {fg}; font-size: 13px; }}"
                f"QWidget#astraControls QComboBox, QWidget#astraControls QDoubleSpinBox "
                f"{{ background: {bg}; color: {fg}; border: 1px solid rgba({hover}, 60); "
                f"border-radius: 4px; padding: 5px 4px; min-height: 20px; font-size: 13px; }}"
                f"QWidget#astraControls QComboBox QAbstractItemView "
                f"{{ background: {bg}; color: {fg}; selection-background-color: rgba({hover}, 40); }}"
            )
            self.theme_actions[theme].setChecked(True)
        self.details_action.setChecked(bool(self.renderer.value(5)))
        has_multiple = self.renderer.value(0) and self.renderer.value(3) > 1
        self.previous.setEnabled(bool(has_multiple))
        self.next.setEnabled(bool(has_multiple))
        if self.instance and self.instance.requested():
            self.present()
        interval = 100 if self.isMinimized() else 16
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)
        frame_ms = (time.monotonic_ns() - started) / 1_000_000
        self.max_frame_ms = max(self.max_frame_ms, frame_ms)
        if now - self.last_diagnostic >= 30:
            if self.last_diagnostic:
                fps = self.canvas.paint_count / (now - self.last_diagnostic)
                samples = sorted(self.canvas.paint_samples)
                paint_p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))] if samples else 0
                print(f"[DESKTOP] render_fps={fps:.1f} max_tick_ms={self.max_frame_ms:.1f} "
                      f"paint_p95_ms={paint_p95:.1f} "
                      f"connected={self.renderer.value(0)} theme={theme} "
                      f"sessions={self.renderer.value(3)}", flush=True)
            self.last_diagnostic = now
            self.frames = 0
            self.canvas.paint_count = 0
            self.max_frame_ms = 0

    def closeEvent(self, event):
        if not self.closed:
            self.closed = True
            self.timer.stop()
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.sync()
            self.client.close()
            if self.tray:
                self.tray.hide()
        event.accept()
        QApplication.quit()


def start_bridge():
    commands = (
        ["systemctl", "--user", "start", "codex-pet-lifecycle.service"],
        ["bash", str(PROJECT / "host/run-background.sh"), "start"],
    )
    for command in commands:
        try:
            result = subprocess.run(command, check=False, timeout=8, capture_output=True)
            if result.returncode == 0:
                return True
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"[DESKTOP] Bridge startup: {type(exc).__name__}", flush=True)
    print("[DESKTOP] Bridge unavailable; the window will keep waiting for IPC.", flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-start-bridge", action="store_true")
    parser.add_argument("--theme", choices=[entry[0] for entry in THEMES.values()])
    parser.add_argument("--seconds", type=float, default=0, help="Exit after a bounded smoke test")
    parser.add_argument("--screenshot", type=Path, help="Capture the window before bounded exit")
    args = parser.parse_args()
    app = QApplication(["codex-assistant"])
    app.setApplicationName("codex-assistant")
    app.setApplicationDisplayName("Codex助手")
    app.setDesktopFileName("codex-assistant")
    app.setStyle("Fusion")
    if not LIBRARY.exists():
        QMessageBox.critical(None, "Codex助手", "缺少桌面渲染库，请运行 desktop/build.sh。")
        return 1
    instance = Instance()
    if not instance.primary:
        instance.close()
        return 0
    window = None
    try:
        if not args.no_start_bridge:
            # systemd owns the process; the desktop never starts another API reader.
            start_bridge()
        window = Window(instance)
        if args.theme:
            window.select_theme(next(index for index, entry in THEMES.items()
                                     if entry[0] == args.theme))
        window.show()
        print("[DESKTOP] Codex助手窗口已启动", flush=True)
        if args.seconds > 0:
            def finish():
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(args.screenshot))
                window.close()
            QTimer.singleShot(round(args.seconds * 1000), finish)
        return app.exec()
    finally:
        if window and not window.closed:
            window.client.close()
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
