"""Offline native UI and Qt interaction checks. Never starts USB or API readers."""
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "desktop"))
sys.path.insert(0, str(PROJECT / "host"))

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import app as desktop_app
from app import AUTO_THEME, NATIVE_THEMES, THEMES, Window, saved_theme
from celestial import model_art
from codex_state import Session
from desktop_ipc import DesktopClient, DesktopHub
from protocol import packet
from renderer import LIBRARY
from text_labels import labels

pytestmark = pytest.mark.skipif(not LIBRARY.exists(), reason="Build desktop/build.sh first")


def sample(theme="glass", state="thinking", index=0):
    sessions = [Session("a", "桌面助手开发", "桌面助手开发", state),
                Session("b", "交互动画验证", "交互动画验证", "done")]
    return packet(12, state=state, total=2, index=index,
                  labels=labels(sessions[index], sessions), theme=theme,
                  balance_quota=11_755_000, today_quota=820_000, duration_seconds=156,
                  input_tps_x10=12800, output_tps_x10=420, usage_stale=False,
                  models=[{"name": "GPT-5", "quota": 640000},
                          {"name": "GPT-5 mini", "quota": 170000}])


@pytest.fixture(scope="module")
def desktop(tmp_path_factory):
    app = QApplication.instance() or QApplication([])
    directory = tmp_path_factory.mktemp("ipc")
    hub = DesktopHub(directory)
    client = DesktopClient(directory)
    settings = QSettings(str(directory / "settings.ini"), QSettings.Format.IniFormat)
    window = Window(settings=settings, client=client)
    window.show()
    window.timer.stop()
    app.processEvents()
    yield app, window, hub
    window.close()
    hub.close()


def advance(window, frames=90, data=None):
    for frame in range(frames):
        if data is not None and frame % 40 == 0:
            window.renderer.feed(data)
        window.renderer.tick(16)
    window.canvas.update()
    QApplication.processEvents()


def tap(window, native_point):
    viewport = window.canvas.viewport()
    point = QPoint(round(viewport.x() + native_point[0] * viewport.width() / 800),
                   round(viewport.y() + native_point[1] * viewport.height() / 480))
    QTest.mouseClick(window.canvas, Qt.MouseButton.LeftButton, pos=point)
    advance(window, 90)


def test_eight_themes_live_animation_and_real_state(desktop):
    _app, window, _hub = desktop
    renderer = window.renderer
    renderer.theme(-1)
    for name, *_rest in NATIVE_THEMES:
        for state, expected in (("thinking", 1), ("done", 4)):
            data = sample(name, state)
            renderer.feed(data)
            advance(window, 120, data)
            before = bytes(renderer.buffer)
            advance(window, 45, data)
            after = bytes(renderer.buffer)
            assert renderer.value(0) == 1
            assert renderer.value(2) == expected
            assert renderer.value(1) == [entry[0] for entry in NATIVE_THEMES].index(name)
            assert before != after, (name, state, "animation frozen")
            assert len(set(after)) > 100, "blank or degenerate framebuffer"


@pytest.mark.parametrize("size", [(800, 514), (640, 418), (1000, 634)])
def test_scaled_click_details_back_and_drag_navigation(desktop, size):
    _app, window, _hub = desktop
    window.resize(*size)
    window.renderer.theme(6)
    data = sample()
    window.renderer.feed(data)
    advance(window, data=data)
    tap(window, (680, 280))
    assert window.renderer.value(5) == 1
    tap(window, (30, 30))
    assert window.renderer.value(5) == 0
    viewport = window.canvas.viewport()
    def point(x):
        return QPoint(round(viewport.x() + x * viewport.width() / 800),
                      round(viewport.y() + 220 * viewport.height() / 480))
    QTest.mousePress(window.canvas, Qt.MouseButton.LeftButton, pos=point(420))
    QTest.mouseMove(window.canvas, point(260))
    QTest.mouseRelease(window.canvas, Qt.MouseButton.LeftButton, pos=point(260))
    assert window.renderer.navigate() == 1
    assert window.renderer.value(2) == 1


def test_ipc_updates_and_disconnect_reconnect(desktop):
    _app, window, hub = desktop
    window.client.subscribe()
    hub.poll()
    hub.publish(sample(index=1))
    window.pump()
    advance(window, 2)
    assert window.renderer.value(4) == 1
    assert window.renderer.value(0) == 1
    assert window.client.navigate(-1)
    assert hub.poll() == -1
    window.renderer.tick(5100)
    advance(window, 2)
    assert window.renderer.value(0) == 0
    assert window.renderer.value(2) == 7
    hub.publish(sample())
    window.pump()
    advance(window, 2)
    assert window.renderer.value(0) == 1
    assert window.renderer.value(2) == 1


def test_theme_persistence_and_software_brightness(desktop):
    _app, window, _hub = desktop
    window.select_theme(3)
    advance(window, 2)
    assert window.settings.value("theme", type=int) == 3
    assert window.renderer.value(1) == 3
    window.renderer.native.pet_brightness(25)
    assert window.renderer.value(6) == 25
    window.renderer.native.pet_brightness(86)


def test_rapid_theme_changes_do_not_drop_input_between_frames(desktop):
    _app, window, _hub = desktop
    window.select_theme(3)
    for _ in range(4):
        window.cycle_theme()
    advance(window, 2)
    assert window.renderer.value(1) == 7
    assert window.settings.value("theme", type=int) == 7


def test_retired_themes_not_in_menu_cycle_or_saved_settings(desktop):
    _app, window, _hub = desktop
    assert not {0, 1} & THEMES.keys()
    assert not {0, 1} & window.theme_actions.keys()
    for theme in (-1, 0, 1, 999):
        assert saved_theme(theme) == AUTO_THEME
    window.select_theme(7)
    window.cycle_theme()
    assert window.selected_theme == AUTO_THEME
    window.cycle_theme()
    assert window.selected_theme == 2


def test_adaptive_theme_display_name_preserves_compatibility_id():
    assert THEMES[AUTO_THEME][0] == "auto"
    assert THEMES[AUTO_THEME][1] == "思想者"


@pytest.mark.parametrize("model,expected", [
    ("gpt-6-sol", "sol"), ("GPT-6 Terra", "terra"),
    ("gpt-6-luna-2026-09-24", "luna"), ("morecode/gpt-6-sol-G", "sol"),
    ("gpt-5.6-sol", "sol"), ("GPT-5.6 Terra", "terra"),
    ("gpt-5.6-luna-2026-09-20", "luna"), ("gpt-6-astra", "astra"),
    ("morecode/gpt-6-astra-G", "astra"), ("gpt-6-astra-A", "astra"),
    ("gpt-5.6-cyber", None), ("gpt-5.4", None), ("astra", None),
    ("gpt-6-astra-unknown", None), ("", None), (None, None),
])
def test_explicit_model_mapping(model, expected):
    assert model_art(model) == expected


def test_adaptive_models_follow_atomic_ipc_and_unknown_fallback(desktop):
    _app, window, hub = desktop
    window.client.subscribe(now=time.monotonic() + 2)
    hub.poll()
    window.select_theme(AUTO_THEME)
    for key, model in (("sol", "gpt-6-sol"), ("terra", "gpt-6-terra"),
                       ("luna", "gpt-6-luna"), ("astra", "gpt-6-astra")):
        hub.publish(sample(), model=model)
        window.pump()
        advance(window, 2)
        assert window.canvas.celestial.current == key
        assert window.renderer.value(1) == 0
        assert window.renderer.value(8) == 0
    window.renderer.details(True)
    advance(window, data=sample())
    assert window.renderer.value(9) == 0
    window.renderer.details(False)
    advance(window, data=sample())
    assert window.renderer.value(9) == 800
    hub.publish(sample(), model="unknown-model")
    window.pump()
    advance(window, 2)
    assert window.selected_theme == AUTO_THEME
    assert window.canvas.celestial.current is None
    assert window.renderer.value(1) == 6


def test_celestial_transition_is_smooth_and_bounded(desktop):
    layer = desktop[1].canvas.celestial
    layer.select("sol", now=10)
    layer.select("astra", now=20)
    assert layer.previous == "sol" and layer.current == "astra"
    assert layer.progress(20) == 0
    assert layer.progress(20+layer.duration/2) == pytest.approx(0.5)
    assert layer.progress(20+layer.duration) == 1


def test_celestial_modes_follow_native_status_and_disconnect(desktop):
    _app, window, hub = desktop
    window.client.subscribe()
    hub.poll()
    window.select_theme(AUTO_THEME)
    for state, expected in (("thinking", "working"), ("working", "working"),
                            ("searching", "working"), ("done", "completed"),
                            ("waiting", None), ("paused", None), ("error", None)):
        hub.publish(sample(state=state), model="gpt-6-astra")
        window.pump()
        advance(window, 2)
        assert window.canvas.celestial.clock.mode == expected, state
    window.renderer.tick(5100)
    advance(window, 2)
    assert window.renderer.value(0) == 0
    assert window.canvas.celestial.clock.mode is None
    hub.publish(sample(state="done"), model="gpt-6-astra")
    window.pump()
    advance(window, 2)
    assert window.canvas.celestial.clock.mode == "completed"


def test_startup_tolerates_unavailable_service(monkeypatch):
    commands = []

    def unavailable(command, **_kwargs):
        commands.append(command)
        raise subprocess.TimeoutExpired(command, 8)

    monkeypatch.setattr(desktop_app.subprocess, "run", unavailable)
    assert not desktop_app.start_bridge()
    assert len(commands) == 2


def test_startup_uses_existing_service_without_duplicate_bridge(monkeypatch):
    commands = []

    def available(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(desktop_app.subprocess, "run", available)
    assert desktop_app.start_bridge()
    assert len(commands) == 1
