import json
from pathlib import Path

import pytest
import bridge
from protocol import LABEL_BYTES

ADDRESS = "80:F1:B2:01:02:03"
HELLO = b"@PET_HELLO 2\n@USAGE_HELLO 2\n@MODEL_PICK_HELLO 1\n@ROTATE_HELLO 1\n"


class Port:
    def __init__(self, *args, **kwargs):
        self.data = HELLO
        self.ready = True
        self.closed = False
        self.writes = []
        self.pending_bytes = 0

    def open(self):
        pass

    def close(self):
        self.closed = True

    def wait_closed(self, timeout=3):
        return self.closed

    def reset_input_buffer(self):
        pass

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, size):
        data, self.data = self.data[:size], self.data[size:]
        return data

    def write(self, data):
        self.writes.append(data)
        return len(data)


@pytest.fixture
def hardware(monkeypatch):
    monkeypatch.setattr(bridge.serial, "Serial", Port)
    monkeypatch.setattr(bridge, "BleSerial", Port)


def test_usb_is_preferred_and_data_waits_for_handshake(tmp_path, hardware):
    conn = bridge.Connection("wired", transport="auto", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    conn.begin_open()
    assert conn.transport == "usb" and conn.port is None
    assert conn.poll_open()
    assert conn.port.writes == [b"?"]
    assert conn.expiry_protocol and conn.model_picker_supported
    conn.close()


def test_missing_usb_uses_only_registered_bluetooth(tmp_path, hardware, monkeypatch):
    def unavailable(_):
        raise OSError("USB absent")
    monkeypatch.setattr(bridge, "resolve_port", unavailable)
    conn = bridge.Connection("auto", transport="auto", peer_file=tmp_path/"peer.json")
    with pytest.raises(RuntimeError, match="connect USB once"):
        conn.begin_open()
    conn.bluetooth_address = ADDRESS
    conn.begin_open()
    assert conn.transport == "ble" and conn.poll_open()
    conn.close()


def test_multiple_usb_devices_never_fall_through_to_another_board(tmp_path, hardware, monkeypatch):
    def ambiguous(_):
        raise RuntimeError("Multiple ESP32 USB devices")
    monkeypatch.setattr(bridge, "resolve_port", ambiguous)
    conn = bridge.Connection("auto", transport="auto", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    with pytest.raises(RuntimeError, match="Multiple"):
        conn.begin_open()


def test_pairing_identity_only_learned_from_usb_and_saved_after_ready(tmp_path, hardware):
    peer = tmp_path/"peer.json"
    conn = bridge.Connection("wired", transport="auto", peer_file=peer)
    identity = f"@BLE_ID {ADDRESS} connected=0 encrypted=0\n".encode()
    conn.transport = "ble"
    conn._learn_ble(identity)
    assert conn.bluetooth_address is None
    conn.transport = "usb"
    conn._learn_ble(identity)
    assert conn.bluetooth_address == ADDRESS
    conn.port = Port()
    conn.poll_transport(10)
    assert not peer.exists()
    pairing = conn._pairing
    conn.poll_transport(11)
    assert not conn._pairing_done
    conn.poll_transport(12)
    assert pairing.closed and conn._pairing_done
    assert json.loads(peer.read_text())["address"] == ADDRESS
    assert peer.stat().st_mode & 0o777 == 0o600
    conn.close()


def test_usb_probe_preserves_ble_until_usb_handshake(tmp_path, hardware, monkeypatch):
    conn = bridge.Connection("wired", transport="auto", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    old = Port()
    conn.port = old
    conn.transport = "ble"
    conn.last_payload = {"state": "done"}
    conn.last_model = 4

    class PendingPort(Port):
        def __init__(self, **kwargs):
            super().__init__()
            self.data = b""

    monkeypatch.setattr(bridge.serial, "Serial", PendingPort)
    now = bridge.time.monotonic()
    conn.poll_transport(now)
    assert conn.port is old and not old.closed
    conn._usb_probe._opening.data = HELLO
    conn.poll_transport(now+.1)
    assert old.closed and conn.transport == "usb"
    assert conn.port is not old and conn.last_model is None and conn.last_payload is None
    assert conn.expiry_protocol and conn.model_picker_supported
    conn.close()


def test_failed_usb_probe_keeps_bluetooth(tmp_path, hardware):
    conn = bridge.Connection("wired", transport="auto", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    old = Port()
    conn.port = old
    conn.transport = "ble"
    probe = bridge.Connection("wired")
    probe.begin_open()
    probe._hello_deadline = 0
    conn._usb_probe = probe
    conn.poll_transport(bridge.time.monotonic())
    assert conn.port is old and not old.closed and conn._usb_probe is None
    conn.close()


def test_unresponsive_usb_gets_a_cooldown_for_ble_fallback(tmp_path, hardware):
    conn = bridge.Connection("wired", transport="auto", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    conn.begin_open()
    assert conn.poll_open()
    conn.retry_after_failure()
    assert conn._usb_unhealthy_until > bridge.time.monotonic() + 10
    conn.begin_open()
    assert conn.transport == "ble"
    conn.close()


def test_ble_backpressure_keeps_latest_state_for_retry(tmp_path, hardware):
    conn = bridge.Connection("wired", transport="ble", bluetooth_address=ADDRESS,
                             peer_file=tmp_path/"peer.json")
    conn.begin_open()
    assert conn.poll_open()
    conn.port.pending_bytes = 4000
    view = dict(state="thinking", labels=bytes(LABEL_BYTES))
    count = len(conn.port.writes)
    conn.send(view)
    conn.send_picker({}, bridge.time.monotonic())
    assert len(conn.port.writes) == count and conn.last_payload is None
    assert conn.last_picker_payload is None
    conn.port.pending_bytes = 0
    view["state"] = "done"
    conn.send(view)
    assert conn.last_payload["state"] == "done"
    conn.close()
