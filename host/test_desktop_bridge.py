import fcntl
from pathlib import Path
import struct

import pytest

import bridge
from bridge import Connection
from codex_state import Session
from desktop_presence import desktop_active
from usage import Usage


class SilentPort:
    in_waiting = 0

    def __init__(self, **_kwargs):
        self.closed = False
        self.writes = []

    def open(self):
        pass

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        pass

    def read(self, _size):
        raise AssertionError("No bytes available: handshake must not block reading")

    def write(self, data):
        self.writes.append(data)
        return len(data)


def test_silent_usb_handshake_is_nonblocking_and_times_out(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(bridge.serial, "Serial", SilentPort)
    monkeypatch.setattr(bridge.time, "monotonic", lambda: now[0])
    connection = Connection("/fake")
    connection.begin_open()
    port = connection._opening
    assert port.dtr and port.rts
    assert not connection.poll_open()
    assert connection.port is None
    assert port.writes == [b"?"]
    now[0] += 0.2
    assert not connection.poll_open()
    assert port.writes == [b"?"]
    now[0] += 7
    with pytest.raises(RuntimeError, match="No pet handshake"):
        connection.poll_open()
    assert port.closed
    assert connection._opening is None


@pytest.mark.parametrize("silent", [False, True])
def test_desktop_gets_real_views_without_working_usb(tmp_path, monkeypatch, silent):
    """One shared usage reader; desktop NAV still works throughout USB failure."""
    clock = [100.0]
    monkeypatch.setattr(bridge.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bridge.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(bridge, "PROJECT", tmp_path)
    monkeypatch.setattr(bridge.signal, "signal", lambda *args: None)
    monkeypatch.setattr(bridge.sys, "argv", ["bridge.py", "--seconds", "2"])
    monkeypatch.setattr(bridge, "labels", lambda *args: bytes(2240))
    readers = []
    hubs = []

    class Detector:
        error = None

        def __init__(self, *args):
            self.sessions = [Session("a", "First", "First", "thinking"),
                             Session("b", "Second", "Second", "done")]
            self.sessions[0].model = "gpt-5.6-sol"
            self.sessions[1].model = "gpt-6-astra"

        def snapshot(self, now):
            return self.sessions

    class Reader:
        def __init__(self, *args):
            readers.append(self)

        def snapshot(self, *args):
            return Usage(balance_quota=12345, input_tps_x10=678, stale=False)

        def close(self):
            pass

    class Hub:
        api_mode_request = None
        rotation_mode_request = None
        membership_expiry_request = None
        model_test_request = None
        def __init__(self):
            self.packets = []
            self.closed = False
            self.calls = 0
            self.models = []
            hubs.append(self)

        def poll(self, now):
            self.calls += 1
            return 1 if self.calls == 2 else 0

        def publish(self, data, now, *, model=None):
            self.packets.append(data)
            self.models.append(model)

        def publish_test_status(self, _status):
            pass

        def publish_rotation_mode(self, mode):
            self.rotation_mode = mode

        def close(self):
            self.closed = True

    def absent(**kwargs):
        raise bridge.serial.SerialException("USB absent")

    monkeypatch.setattr(bridge, "Detector", Detector)
    monkeypatch.setattr(bridge, "UsageRouter", Reader)
    monkeypatch.setattr(bridge, "DesktopHub", Hub)
    monkeypatch.setattr(bridge.serial, "Serial", SilentPort if silent else absent)
    assert bridge.main() == 0
    assert len(readers) == len(hubs) == 1
    assert hubs[0].closed
    packets = hubs[0].packets
    assert len(packets) >= 3
    assert packets[0][10:12] == b"\x02\x00"
    assert packets[-1][10:12] == b"\x02\x01"
    assert hubs[0].models[0] == "gpt-5.6-sol"
    assert hubs[0].models[-1] == "gpt-6-astra"
    assert hubs[0].rotation_mode == "active"
    usage_offset = 8 + 5 + 2240 + 7
    assert struct.unpack_from("<I", packets[-1], usage_offset)[0] == 12345


def test_desktop_presence_tracks_lock_not_stale_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert not desktop_active()
    with (tmp_path / "codex-pet/desktop.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert desktop_active()
    assert not desktop_active()
