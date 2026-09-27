"""Conversation rotation controls without Unix Socket access."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PROJECT / "desktop"), str(PROJECT / "host")]

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from app import Window


class Client:
    model = "gpt-6-astra"
    rotation_mode = "active"
    model_test_status = {"available": False, "active": False, "step": 0,
                         "model": 0, "error": ""}
    model_test_updated = 0

    def __init__(self):
        self.requests = []

    def set_rotation_mode(self, mode):
        self.requests.append(mode)
        return True

    def subscribe(self, *_args):
        return True

    def receive(self):
        return []

    def navigate(self, _delta):
        return True

    def close(self):
        pass


def test_motion_menu_has_three_rotation_modes_and_tracks_host(tmp_path):
    application = QApplication.instance() or QApplication([])
    client = Client()
    settings = QSettings(str(tmp_path / "desktop.ini"), QSettings.Format.IniFormat)
    window = Window(client=client, settings=settings)
    window.timer.stop()
    try:
        assert [action.text() for action in window.rotation_actions.values()] == [
            "关闭滚动", "只滚动执行任务", "滚动全部"]
        assert window.rotation_actions["active"].isChecked()
        window.rotation_actions["all"].trigger()
        assert client.requests == ["all"]
        client.rotation_mode = "off"
        window.pump()
        application.processEvents()
        assert window.rotation_actions["off"].isChecked()
    finally:
        window.close()
        application.processEvents()
