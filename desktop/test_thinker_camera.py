"""Contracts for the fixed-world-coordinate Thinker camera path."""
import ctypes
import itertools
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pytest

from thinker_transition import ThinkerTransition


MODEL_IDS = (1, 2, 3, 4)
ENDPOINT_PROJECTIONS = {
    1: (278.0, 280.0),
    2: (241.0, 207.0),
    3: (237.0, 203.0),
}
ASTRA_COMPOSITION = np.array(((357.0, 92.0), (303.0, 47.0), (340.0, 152.0)))


@pytest.fixture(scope="module")
def camera():
    engine = ThinkerTransition()
    function = engine.library.pet_thinker_camera
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_float,
                         ctypes.POINTER(ctypes.c_float)]
    function.restype = None
    return function


def sample(camera, source, target, progress):
    values = np.empty(21, dtype=np.float32)
    camera(source, target, ctypes.c_float(progress),
           values.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
    return values


@pytest.mark.parametrize("source,target", itertools.permutations(MODEL_IDS, 2))
def test_all_twelve_routes_register_the_expected_endpoint_composition(camera, source, target):
    start = sample(camera, source, target, 0.0)
    end = sample(camera, source, target, 1.0)
    assert np.all(np.isfinite(start))
    assert np.all(np.isfinite(end))
    if source != 4:
        assert np.allclose(start[6 + (source - 1) * 3:8 + (source - 1) * 3],
                           ENDPOINT_PROJECTIONS[source], atol=1.0)
    if target != 4:
        assert np.allclose(end[6 + (target - 1) * 3:8 + (target - 1) * 3],
                           ENDPOINT_PROJECTIONS[target], atol=1.0)
    if source == 4:
        assert np.allclose(start[6:15].reshape(3, 3)[:, :2],
                           ASTRA_COMPOSITION, atol=1.0)
    if target == 4:
        assert np.allclose(end[6:15].reshape(3, 3)[:, :2],
                           ASTRA_COMPOSITION, atol=1.0)


@pytest.mark.parametrize("source,target", itertools.permutations(MODEL_IDS, 2))
@pytest.mark.parametrize("progress", [0.0, 0.17, 0.5, 0.83, 1.0])
def test_reverse_route_is_the_same_camera_trajectory(camera, source, target, progress):
    forward = sample(camera, source, target, progress)
    reverse = sample(camera, target, source, 1.0 - progress)
    assert np.allclose(forward, reverse, rtol=2e-5, atol=2e-3)


@pytest.mark.parametrize("source,target", itertools.permutations(MODEL_IDS, 2))
def test_route_has_finite_positive_focal_and_depth(camera, source, target):
    for progress in np.linspace(0.0, 1.0, 21):
        values = sample(camera, source, target, progress)
        assert np.all(np.isfinite(values))
        assert values[3] > 0.0
        assert np.all(values[8::3] > 0.0)


@pytest.mark.parametrize("source,target", itertools.permutations(MODEL_IDS, 2))
def test_camera_velocity_tends_to_zero_at_both_ends(camera, source, target):
    epsilon = 1.0e-3
    start = sample(camera, source, target, 0.0)[:6]
    near_start = sample(camera, source, target, epsilon)[:6]
    near_end = sample(camera, source, target, 1.0 - epsilon)[:6]
    end = sample(camera, source, target, 1.0)[:6]
    assert np.linalg.norm(near_start - start) < 0.2
    assert np.linalg.norm(end - near_end) < 0.2


def test_astra_midpoint_exposes_near_far_parallax_and_not_uniform_layer_scaling(camera):
    endpoint = sample(camera, 1, 4, 1)
    assert np.allclose(endpoint[15:17], (350, 172), atol=.01)
    assert np.allclose(endpoint[18:20], (350, 172), atol=.01)
    midpoint = sample(camera, 1, 4, 0.5)
    near, far = midpoint[15:17], midpoint[18:20]
    assert np.linalg.norm(near - far) > 0.1

    points = midpoint[6:15].reshape(3, 3)[:, :2]
    distances = np.linalg.norm(points[1:] - points[0], axis=1)
    assert distances[0] > 0.0 and distances[1] > 0.0
    assert not np.isclose(distances[0] / distances[1],
                          (278.0 - 241.0) / (278.0 - 237.0), rtol=0.02)
