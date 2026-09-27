"""Temporal and clipping checks on actual celestial pixels, without live IPC."""
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))

import numpy as np
import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from celestial import CelestialLayer, MODELS
from celestial_motion import CENTERS, Motion, MotionClock


@pytest.fixture(scope="module")
def layer():
    app = QApplication.instance() or QApplication([])
    scene = CelestialLayer()
    scene.preload()
    yield scene
    assert app is QApplication.instance()


def render(scene, now, detail_left=800, scale=1):
    image = QImage(round(800 * scale), round(480 * scale), QImage.Format.Format_RGB888)
    image.fill(QColor("#123456"))
    painter = QPainter(image)
    painter.scale(scale, scale)
    scene.paint(painter, detail_left, now=now)
    painter.end()
    return np.asarray(image.constBits()).reshape(image.height(), image.bytesPerLine())[
        :, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


@pytest.mark.parametrize("key", MODELS)
@pytest.mark.parametrize("state", [1, 4])
def test_both_modes_really_animate_and_frames_stay_continuous(layer, key, state):
    layer.select(key, now=-10)
    layer.clock = MotionClock()
    layer.set_state(state, True, now=0)
    first = render(layer, 8)[95:397, 35:500].astype(float)
    adjacent = render(layer, 8 + 1 / 60)[95:397, 35:500].astype(float)
    later = render(layer, 12)[95:397, 35:500].astype(float)
    next_loop = render(layer, 25)[95:397, 35:500].astype(float)
    long_delta = np.abs(later - first).mean()
    assert first.std() > 25, (key, "missing celestial body")
    assert long_delta > 0.3, (key, state, "animation visually frozen")
    assert np.abs(next_loop - later).mean() > 0.3
    assert np.abs(adjacent - first).mean() < long_delta * 0.22
    surface = layer.pictures[key]
    assert np.isfinite(surface.map_x).all() and np.isfinite(surface.map_y).all()
    if key == "sol":
        sx, sy, _source_radius = CENTERS["sol"]
        cx, cy, scale = surface.solar_pose()
        mapped_radius = np.hypot(surface.map_x - sx, surface.map_y - sy)
        maximum = np.hypot(max(cx, 624 - cx), max(cy, 416 - cy)) / scale
        assert mapped_radius.max() < maximum + 20
        disc = np.hypot(surface.grid_x - cx, surface.grid_y - cy) < 128 * scale
        assert mapped_radius[disc].max() < 145
    else:
        assert surface.map_x.min() > -25 and surface.map_x.max() < 650
        assert surface.map_y.min() > -25 and surface.map_y.max() < 442


@pytest.mark.parametrize("state", [1, 4])
@pytest.mark.parametrize("scale", [0.8, 1, 1.6, 2])
def test_solar_disc_is_large_centered_and_visibly_crosses_bottom_edge(layer, state, scale):
    layer.select("sol", now=-10)
    layer.clock = MotionClock()
    layer.set_state(state, True, now=0)
    for now in (8, 23, 40):
        image = render(layer, now, scale=scale)
        row = image[round((64 + 280) * scale), :round(550 * scale)]
        bright = np.flatnonzero((row[:, 0] > 165) & (row[:, 1] > 75))
        left, right = bright[0] / scale, bright[-1] / scale
        assert right - left > 470
        assert abs((left + right) / 2 - 278) < 12
        bottom = image[-max(1, round(4 * scale)):, round(140 * scale):round(416 * scale)]
        assert bottom[..., 0].mean() > 160
        assert bottom[..., 1].mean() > 70


@pytest.mark.parametrize("key", MODELS)
def test_working_completed_transition_has_no_visual_pop(layer, key):
    layer.select(key, now=-10)
    layer.clock = MotionClock()
    layer.set_state(1, True, now=0)
    before = render(layer, 8)
    uninterrupted = render(layer, 8 + 1 / 60)
    layer.set_state(4, True, now=8)
    at_switch = render(layer, 8)
    assert np.array_equal(before, at_switch)
    after = render(layer, 8 + 1 / 60)
    assert np.abs(after.astype(float) - uninterrupted).mean() < 0.15
    assert np.abs(after.astype(float) - before).mean() < 2


@pytest.mark.parametrize("key", MODELS)
@pytest.mark.parametrize("state", [1, 4])
def test_motion_is_salient_within_two_seconds_at_minimum_window_size(layer, key, state):
    layer.select(key, now=-10)
    layer.clock = MotionClock()
    layer.set_state(state, True, now=0)
    for now in (3, 8, 15, 24):
        before = render(layer, now, scale=0.8)[80:315, 30:400].astype(float)
        after = render(layer, now + 2, scale=0.8)[80:315, 30:400].astype(float)
        delta = np.abs(after - before).mean(axis=2)
        assert delta.mean() > 3, (key, state, now, "motion too subtle")
        assert (delta > 12).mean() > 0.08, (key, state, now, "only tiny points animate")


@pytest.mark.parametrize("key", MODELS)
def test_state_changes_motion_design_not_just_playback_speed(layer, key):
    surface = layer.pictures[key]
    working = surface.frame(Motion(9, 1, 1)).copy()
    completed = surface.frame(Motion(9, 0, 1)).copy()
    a = np.asarray(working.constBits()).astype(float)
    b = np.asarray(completed.constBits()).astype(float)
    assert np.abs(a - b).mean() > 1, (key, "identical working and completed material")


@pytest.mark.parametrize("scale", [0.8, 1, 1.5, 1.6, 2])
@pytest.mark.parametrize("left", [0, 271, 800])
@pytest.mark.parametrize("key", ["astra", "sol"])
def test_header_telemetry_and_sliding_statistics_remain_uncovered(layer, scale, left, key):
    layer.select(key, now=-10)
    layer.clock = MotionClock()
    layer.set_state(1, True, now=0)
    pixels = render(layer, 20, detail_left=left, scale=scale)
    untouched = np.array([0x12, 0x34, 0x56])
    assert np.all(pixels[:round(64 * scale)] == untouched)
    assert np.all(pixels[:, round(min(556, left) * scale) + 1:] == untouched)
    if left == 0:
        assert np.all(pixels == untouched)


@pytest.mark.parametrize("key", MODELS)
def test_settled_disconnect_freezes_actual_pixels(layer, key):
    layer.select(key, now=-10)
    layer.clock = MotionClock()
    layer.set_state(1, True, now=0)
    layer.set_state(1, False, now=8)
    before, later = render(layer, 35), render(layer, 45)
    assert np.abs(before.astype(float) - later).mean() < 0.01
