import importlib.util
import io
from pathlib import Path

import pytest


@pytest.fixture
def smoke(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "tools/preview/sd_camera_smoke.py"
    spec = importlib.util.spec_from_file_location("sd_camera_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Port:
        def __init__(self, **kwargs):
            self.dtr = self.rts = None
            self.data = b""
            self.sent = []

        def open(self):
            assert self.dtr is True and self.rts is True, "P4 USB must not reset on open"

        @property
        def in_waiting(self):
            return len(self.data)

        def read(self, size):
            data, self.data = self.data[:size], self.data[size:]
            return data

        def write(self, data):
            self.sent.append(data)

    monkeypatch.setattr(module.serial, "Serial", Port)
    return module


def test_camera_probe_preserves_p4_reset_lines_and_waits_for_handshake(smoke):
    device = smoke.Device("fake", io.StringIO())
    device.pump()
    assert device.serial.sent == []
    device.serial.data = b"@PET_HELLO 2\n"
    device.pump()
    device.pump()
    assert len(device.serial.sent) == 1
    # Full status is allowed, artwork packets (type 3) are never sent.
    assert device.serial.sent[0][8] == 1


def test_camera_routes_cover_all_directed_pairs(smoke):
    edges = list(zip(smoke.ROUTE, smoke.ROUTE[1:]))
    assert len(edges) == 12
    assert set(edges) == {(a, b) for a in range(1, 5) for b in range(1, 5) if a != b}


@pytest.mark.parametrize("partial", [0, 1, 2, 3])
def test_rotation_accepts_complete_full_and_partial_checks(smoke, partial):
    smoke.validate_rotation_checks([{
        "partial_checks": str(partial), "rotation_checked": str(480*800*(1+partial)),
    }])


@pytest.mark.parametrize("presentation", [
    [], [{}], [{"rotation_checked": "0"}],
    [{"rotation_checked": "384001"}],
    [{"partial_checks": "3", "rotation_checked": "384000"}],
    [{"partial_checks": "4", "rotation_checked": "1920000"}],
])
def test_rotation_rejects_missing_or_incomplete_checks(smoke, presentation):
    with pytest.raises(RuntimeError):
        smoke.validate_rotation_checks(presentation)


@pytest.mark.parametrize("line", [
    "@ART_STAT frames=1 bad=0 uptime_s=5",
    "@ART_STAT frames=0 bad=1 uptime_s=5",
    "@STAT crc_bad=1",
    "W sd_media: SD camera clip missing",
    "W sd_media: SD local media disabled",
    "@PRESENT errors=1 rotation_errors=0",
    "@PRESENT errors=0 rotation_errors=1",
    "@PRESENT errors=0 rotation_errors=0 partial_errors=1",
    "@LOOP_RESUME clip=3 frame=0 expected_clip=3 expected_frame=12 joined=0 continuous=0",
])
def test_camera_probe_rejects_false_local_success(smoke, line):
    device = smoke.Device("fake", io.StringIO())
    device.serial.data = (line + "\n").encode()
    with pytest.raises(RuntimeError):
        device.pump()


def test_camera_probe_rejects_reboot_and_handles_partial_lines(smoke):
    device = smoke.Device("fake", io.StringIO())
    device.serial.data = b"@ART_STAT frames=0 bad=0 uptime_s=10"
    device.pump()
    assert not device.stats
    device.serial.data = b"\n"
    device.pump()
    assert len(device.stats) == 1
    device.serial.data = b"@ART_STAT frames=0 bad=0 uptime_s=5\n"
    with pytest.raises(RuntimeError, match="rebooted"):
        device.pump()


@pytest.mark.parametrize("handoff", [
    "sequence=101 crc=12345679 pixel_equal=1 ordered=1",
    "sequence=102 crc=12345678 pixel_equal=1 ordered=1",
    "sequence=101 crc=12345678 pixel_equal=0 ordered=1",
    "sequence=101 crc=12345678 pixel_equal=1 ordered=0",
])
def test_camera_probe_rejects_nonidentical_displayed_handoff(smoke, handoff):
    device = smoke.Device("fake", io.StringIO())
    device.serial.data = ("@LANDING_FRAME sequence=100 crc=12345678 pixel_equal=1\n"
                          "@LOOP_HANDOFF " + handoff + "\n").encode()
    with pytest.raises(RuntimeError, match="not identical"):
        device.pump()


@pytest.mark.parametrize("sequence", [100, 2**32-1])
def test_camera_probe_accepts_identical_adjacent_displayed_frames(smoke, sequence):
    device = smoke.Device("fake", io.StringIO())
    device.serial.data = (
        f"@LANDING_FRAME sequence={sequence} crc=12345678 pixel_equal=1\n"
        f"@LOOP_HANDOFF sequence={(sequence+1)%2**32} crc=12345678 pixel_equal=1 ordered=1\n"
    ).encode()
    device.pump()
    assert len(device.handoffs) == 1
