"""Focused offline checks for persistent Astra motion controls."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "desktop"))
sys.path.insert(0, str(PROJECT / "host"))

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

import app as desktop_app
from app import Window
from desktop_ipc import DesktopClient, DesktopHub
from renderer import LIBRARY

pytestmark = pytest.mark.skipif(not LIBRARY.exists(), reason="Build desktop/build.sh first")


@pytest.fixture
def desktop(tmp_path):
    app = QApplication.instance() or QApplication([])
    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)
    hub = DesktopHub(directory)
    client = DesktopClient(directory)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = Window(settings=settings, client=client)
    window.timer.stop()
    window.show()
    app.processEvents()
    yield app, window, hub, client, settings
    window.close()
    client.close()
    hub.close()
    app.processEvents()


def test_motion_menu_embeds_controls_and_corrects_period_bounds(desktop):
    app, window, _hub, _client, _settings = desktop
    controls = window.astra_controls

    assert controls.parent() is window.motion_menu
    assert [controls.style_box.itemData(index) for index in range(controls.style_box.count())] == [
        "classic", "linked"
    ]
    assert controls.minimum.minimum() == 1
    assert controls.minimum.maximum() == 120
    assert controls.maximum.minimum() == 1
    assert controls.maximum.maximum() == 120

    controls.minimum.setValue(30)
    assert controls.maximum.value() == 30
    controls.maximum.setValue(8)
    assert controls.minimum.value() == 8
    assert window.canvas.celestial.astra_options.minimum == 8
    assert window.canvas.celestial.astra_options.maximum == 8
    app.processEvents()


def test_profiles_persist_independently_and_reset_only_active_profile(desktop):
    _app, window, _hub, _client, settings = desktop

    assert window.read_astra_options("classic").minimum == 5
    assert window.read_astra_options("classic").maximum == 7.5
    assert window.read_astra_options("linked").minimum == 10
    assert window.read_astra_options("linked").maximum == 15

    window.set_astra_style("classic")
    window.set_astra_periods(3, 6)
    window.set_astra_style("linked")
    window.set_astra_periods(12, 18)
    assert settings.value("astra/classic/minimum", type=float) == 3
    assert settings.value("astra/classic/maximum", type=float) == 6
    assert settings.value("astra/linked/minimum", type=float) == 12
    assert settings.value("astra/linked/maximum", type=float) == 18

    window.reset_astra_periods()
    assert window.canvas.celestial.astra_options.style == "linked"
    assert (window.canvas.celestial.astra_options.minimum,
            window.canvas.celestial.astra_options.maximum) == (10, 15)
    assert (window.read_astra_options("classic").minimum,
            window.read_astra_options("classic").maximum) == (3, 6)


def test_reopening_reads_saved_options(desktop, tmp_path):
    app, window, hub, client, settings = desktop
    window.set_astra_style("classic")
    window.set_astra_periods(4.5, 9)
    window.close()
    client.close()
    hub.close()
    app.processEvents()

    reopened_client = DesktopClient(tmp_path / "ipc")
    reopened = Window(settings=settings, client=reopened_client)
    reopened.timer.stop()
    assert reopened.canvas.celestial.astra_options.style == "classic"
    assert reopened.canvas.celestial.astra_options.minimum == 4.5
    assert reopened.canvas.celestial.astra_options.maximum == 9
    reopened.close()
    reopened_client.close()


def test_preview_menu_stays_visible_and_restores_adaptive_without_telemetry_changes(desktop):
    app, window, _hub, client, _settings = desktop
    client.model = "gpt-5.6-luna"
    window.renderer.theme(6)
    telemetry = [0, 2, 3, 4, 5, 6, 7, 8, 9]
    before = [window.renderer.value(index) for index in telemetry]

    window.motion_menu.popup(window.motion_button.mapToGlobal(window.motion_button.rect().bottomLeft()))
    app.processEvents()
    assert window.motion_menu.isVisible()
    assert window.astra_controls.isVisible()

    window.astra_controls.style_box.setCurrentIndex(
        window.astra_controls.style_box.findData("classic"))
    window.astra_controls.minimum.setValue(4)
    window.astra_controls.maximum.setValue(9)
    app.processEvents()
    assert [window.renderer.value(index) for index in telemetry] == before

    window.motion_menu.hide()
    app.processEvents()
    window.renderer.tick(32)
    assert not window.motion_preview_active
    assert window.canvas.adaptive
    assert not window.canvas.motion_preview
    assert window.renderer.value(1) == 0
    assert window.canvas.celestial.current == desktop_app.model_art(client.model) == "luna"
