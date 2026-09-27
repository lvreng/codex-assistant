import importlib
from pathlib import Path

import pytest


@pytest.fixture
def timing(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools/preview"))
    return importlib.import_module("astra_timing_smoke")


def device_with_samples(timing, *, count=3, fps=30, minimum_fps=22):
    device = timing.TimingDevice.__new__(timing.TimingDevice)
    device.minimum_fps = minimum_fps
    device.samples = count
    base = dict(style=1, min_x10=200, max_x10=300, clip=7, layers=1, epoch=5,
                source_milli=0, bg_milli=0, rate_milli=500, uptime_ms=0, output=0,
                blends=0, compose_max_us=9000, blend_max_us=9000,
                compose_total_us=0, read_total_us=0, jpeg_total_us=0, crc_total_us=0,
                simd_pixels=131968, frame_exchange=1)
    device.timing = [base.copy()]

    def send(*_):
        for step in range(1, count+1):
            frames = fps * step * 2
            device.timing.append(dict(base, uptime_ms=step*2000, output=frames,
                                      source_milli=step*30000, bg_milli=step*60000,
                                      blends=frames, compose_total_us=frames*8000,
                                      read_total_us=frames*17000, jpeg_total_us=frames*4000,
                                      crc_total_us=frames*3000))

    device.send = send
    device.until = lambda condition, *_: condition() or pytest.fail("Insufficient samples")
    return device


@pytest.mark.parametrize("samples", [3, 11])
def test_stage_costs_use_completed_frames_and_stable_window(timing, samples):
    device = device_with_samples(timing, count=samples)
    result = device.measure(1, 200)
    assert result["output_fps"] == 30
    assert result["measurement_seconds"] == (samples-1)*2
    assert result["source_fps"] == 15
    assert result["background_source_fps"] == 30
    assert result["compose_mean_ms"] == 8
    assert result["read_mean_ms"] == 17
    assert result["jpeg_mean_ms"] == 4
    assert result["crc_mean_ms"] == 3
    assert result["read_mean_includes_crc"] is True
    assert result["simd_pixels"] == 131968
    assert result["frame_exchange"] == 1


def test_fps_threshold_is_not_nominal_source_fps(timing):
    device = device_with_samples(timing, fps=28, minimum_fps=29)
    with pytest.raises(RuntimeError, match="Local playback is too slow: 28.00"):
        device.measure(1, 200)


def test_reset_during_measurement_is_rejected(timing):
    device = device_with_samples(timing)
    send = device.send

    def reset(*args):
        send(*args)
        device.timing[-1]["epoch"] += 1

    device.send = reset
    with pytest.raises(RuntimeError, match="Playback restarted"):
        device.measure(1, 200)
