"""Focused numeric checks for the desktop celestial motion state machine."""
from pathlib import Path
import sys

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "desktop"))

from celestial_motion import MotionClock, animation_mode


@pytest.mark.parametrize("state", [1, 2, 8])
def test_working_state_mapping(state):
    assert animation_mode(state) == "working"


def test_completed_and_ignored_state_mapping():
    assert animation_mode(4) == "completed"
    for state in (0, 3, 5, 6, 7, 9, None, "working"):
        assert animation_mode(state) is None
        assert animation_mode(state, connected=False) is None


def test_repeated_packets_do_not_restart_or_change_phase():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    before = clock.sample(0.8)

    clock.set_state(1, True, 0.8)
    after = clock.sample(1.2)

    fresh = MotionClock()
    fresh.set_state(1, True, 0.0)
    expected = fresh.sample(1.2)
    assert after == expected
    assert before.phase < after.phase


def test_switch_preserves_position_and_velocity():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    before = clock.sample(1.0)
    before_next = clock.sample(1.0 + 1e-5)
    clock.set_state(4, True, 1.0)
    at_switch = clock.sample(1.0)
    after = clock.sample(1.0 + 1e-5)

    before_velocity = (before_next.phase - before.phase) / 1e-5
    after_velocity = (after.phase - at_switch.phase) / 1e-5
    assert at_switch.phase == pytest.approx(before.phase)
    assert after_velocity == pytest.approx(before_velocity, rel=1e-4, abs=1e-5)


def test_switch_preserves_activity_velocity_to_avoid_a_texture_kick():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    dt = 1e-6
    before, before_next = clock.sample(0.8), clock.sample(0.8 + dt)
    clock.set_state(4, True, 0.8)
    at_switch, after = clock.sample(0.8), clock.sample(0.8 + dt)
    assert at_switch == before
    assert (after.activity - at_switch.activity) / dt == pytest.approx(
        (before_next.activity - before.activity) / dt, abs=1e-5)


def test_frequent_reversals_keep_motion_bounded_and_forward():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    previous = clock.sample(0.0)
    for index in range(1, 401):
        now = index * 0.025
        clock.set_state(4 if index % 2 else 1, True, now)
        current = clock.sample(now)
        assert current.phase >= previous.phase
        assert 0.0 <= current.activity <= 1.0
        assert 0.0 <= current.visibility <= 1.0
        assert current.phase - previous.phase <= 0.05
        previous = current


def test_disconnect_gradually_freezes_without_completed_state():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    before_disconnect = clock.sample(1.0)
    clock.set_state(1, False, 1.0)
    samples = [clock.sample(1.0 + index) for index in range(11)]

    assert samples[0] == before_disconnect
    assert samples[-1].visibility < 1e-6
    assert samples[-1].activity < 1e-6
    assert all(left.phase <= right.phase for left, right in zip(samples, samples[1:]))
    assert samples[-1].phase - samples[0].phase < 0.65
    assert clock.mode is None


def test_phase_remains_monotonic_over_long_running_clock():
    clock = MotionClock()
    clock.set_state(1, True, 0.0)
    phases = [clock.sample(float(seconds)).phase for seconds in range(1001)]

    assert all(left <= right for left, right in zip(phases, phases[1:]))
    assert phases[-1] > 999.0
