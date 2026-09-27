import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from device_artwork import DeviceArtwork
from celestial_motion import MotionClock


@pytest.fixture(scope="module")
def artwork():
    app = QApplication.instance() or QApplication([])
    result = DeviceArtwork()
    yield result
    assert app is QApplication.instance()


@pytest.mark.parametrize("key", ["sol", "terra", "luna", "astra"])
@pytest.mark.parametrize("state", [1, 4])
def test_device_jpeg_is_baseline_bounded_and_visibly_animated(artwork, key, state):
    artwork.layer.clock = MotionClock()
    artwork.configure(key, state, True, now=0)
    a, b = artwork.frame(8), artwork.frame(12)
    assert len(a) < 96 * 1024
    assert b"\xff\xc0" in a and b"\xff\xc2" not in a
    first = cv2.imdecode(np.frombuffer(a, dtype=np.uint8), cv2.IMREAD_COLOR)
    later = cv2.imdecode(np.frombuffer(b, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert first.shape == (416, 560, 3)
    assert first.std() > 20
    assert np.abs(first.astype(float) - later).mean() > 0.4
    if key == "sol":
        assert first[-4:, 140:416, 2].mean() > 160


def test_unknown_model_clears_and_preview_does_not_fake_connection(artwork):
    artwork.configure(None, 7, False, now=50)
    assert artwork.frame(51) == b""
    artwork.configure("astra", 7, False, preview=True, now=52)
    assert artwork.frame(53)
    assert artwork.layer.clock.mode == "working"
    artwork.configure("astra", 7, False, preview=False, now=54)
    assert artwork.layer.clock.mode is None


def test_astra_variants_periods_reach_same_desktop_engine(artwork):
    artwork.configure("astra", 1, True, "classic", 5, 7.5, now=60)
    assert artwork.layer.astra_options.style == "classic"
    classic = artwork.frame(65)
    artwork.configure("astra", 1, True, "linked", 20, 30, now=66)
    assert artwork.layer.astra_options.maximum == 30
    assert artwork.layer.astra_options.minimum == 20
    assert artwork.frame(69) != classic
