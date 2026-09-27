import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import Window


class FakeClient:
    def __init__(self):
        self.model = "gpt-5.6-terra"
        self.model_test_status = {"available": True, "active": False, "step": 0,
                                  "model": 0, "error": ""}
        self.model_test_updated = time.monotonic()
        self.requests = []

    def subscribe(self, _now=None): return True
    def receive(self): return []
    def navigate(self, _delta): return True
    def request_model_test(self, start):
        self.requests.append(start)
        return True
    def close(self): pass


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    client = FakeClient()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    result = Window(settings=settings, client=client)
    result.timer.stop()
    result.update_model_test_ui()
    yield result, client
    result.close()


def test_toolbar_and_motion_menu_share_action_and_preserve_model(window):
    win, client = window
    assert win.model_test_button.defaultAction() is win.model_test_action
    assert win.motion_model_test_action is win.model_test_action
    assert win.model_test_action.text() == "ESP32 四模型切换测试"
    assert client.model == "gpt-5.6-terra"
    win.model_test_action.trigger()
    assert client.requests == [True]
    assert client.model == "gpt-5.6-terra"


def test_status_drives_stop_label_and_stop_request(window):
    win, client = window
    client.model_test_status.update(active=True, step=2, model=2)
    client.model_test_updated = time.monotonic()
    win.update_model_test_ui()
    assert "测试 2/4 Terra" in win.model_test_action.text()
    win.model_test_action.trigger()
    assert client.requests == [False]


def test_offline_and_stale_disable_controls(window):
    win, client = window
    client.model_test_status["available"] = False
    win.update_model_test_ui()
    assert not win.model_test_action.isEnabled()
    client.model_test_status["available"] = True
    client.model_test_updated = time.monotonic() - 5
    win.update_model_test_ui()
    assert not win.model_test_action.isEnabled()


def test_pending_blocks_repeated_requests_but_ack_allows_immediate_stop(window):
    win, client = window
    win.model_test_action.hover()
    assert not client.requests
    win.toggle_model_test()
    win.toggle_model_test()
    assert client.requests == [True]
    assert not win.model_test_action.isEnabled()
    client.model_test_status.update(active=True, step=1, model=1)
    client.model_test_updated = time.monotonic()
    win.update_model_test_ui()
    assert win.model_test_action.isEnabled()
    win.model_test_action.trigger()
    assert client.requests == [True, False]


def test_send_failure_has_visible_tooltip(window):
    win, client = window
    client.request_model_test = lambda _start: False
    win.model_test_action.trigger()
    assert "上位机未连接" in win.model_test_action.toolTip()
