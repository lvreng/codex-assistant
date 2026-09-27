import bridge
from bridge import Connection
from pathlib import Path
import pytest


def test_auto_port_uses_stable_usb_identity(monkeypatch):
    stable = Path("/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_TEST-if00")
    monkeypatch.setattr(Path, "glob", lambda *_args: [stable])
    assert bridge.resolve_port("auto") == str(stable)
    assert bridge.resolve_port("/dev/ttyACM9") == "/dev/ttyACM9"
    monkeypatch.setattr(Path, "glob", lambda *_args: [])
    with pytest.raises(RuntimeError, match="Waiting"):
        bridge.resolve_port("auto")
    monkeypatch.setattr(Path, "glob", lambda *_args: [stable, Path(str(stable) + "2")])
    with pytest.raises(RuntimeError, match="Multiple"):
        bridge.resolve_port("auto")


def test_bridge_has_no_usb_video_fallback():
    import ast
    import inspect

    source = ast.parse(inspect.getsource(bridge))
    imports = [node.module for node in ast.walk(source) if isinstance(node, ast.ImportFrom)]
    assert "art_stream" not in imports
    assert not any(isinstance(node, ast.Name) and node.id in ("ArtStream", "art_packets", "artwork")
                   for node in ast.walk(source))


class FakePort:
    def __init__(self, data):
        self.data = data

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result


def test_connection_receives_session_navigation():
    connection = Connection("/dev/null")
    connection.port = FakePort(b"@NAV 1\n@NAV -1\n@NAV 1\n")

    assert connection.receive() == 1


def test_goodbye_notifies_device_without_waiting_for_heartbeat():
    from protocol import disconnect_packet
    class Writer:
        def write(self, data):
            self.data = data
    connection = Connection("unused")
    connection.goodbye()  # Disconnected shutdown is harmless.
    connection.port = Writer()
    connection.goodbye()
    assert connection.port.data == disconnect_packet(1)


def test_model_picker_commands_are_bounded_and_capability_gated():
    connection = Connection("/dev/null")
    connection.port = FakePort(
        b"@MODEL_PICK_HELLO 1\n@MODEL_PICK pin 44 7 0\n@MODEL_PICK open 44 7 0\n"
        b"@MODEL_PICK cancel 44 8 0\n"
        b"@MODEL_PICK confirm 44 7 6\n@MODEL_PICK confirm 4294967296 7 6\n"
        b"@MODEL_PICK confirm 44 65536 6\n@MODEL_PICK confirm 44 7 7\n")
    connection.receive()
    assert connection.model_picker_supported
    assert connection.model_picker_requests == [
        ("pin", 44, 7, 0), ("open", 44, 7, 0), ("cancel", 44, 8, 0),
        ("confirm", 44, 7, 6)]


def test_connection_open_keeps_usb_control_lines_asserted(monkeypatch):
    class HandshakePort:
        created = None

        def __init__(self, **_kwargs):
            self.dtr = None
            self.rts = None
            self.port = None
            self.is_open = False
            self.closed = False
            self.writes = []
            self.data = b"@PET_HELLO 2\n"
            HandshakePort.created = self

        def open(self):
            self.is_open = True

        def close(self):
            self.closed = True

        def reset_input_buffer(self):
            pass

        @property
        def in_waiting(self):
            return len(self.data)

        def read(self, size):
            result, self.data = self.data[:size], self.data[size:]
            return result

        def write(self, data):
            self.writes.append(data)
            return len(data)

    monkeypatch.setattr(bridge.serial, "Serial", HandshakePort)
    connection = Connection("/dev/ttyACM0")

    connection.open()

    port = HandshakePort.created
    assert port.dtr is True
    assert port.rts is True
    assert port.port == "/dev/ttyACM0"
    assert port.is_open
    assert not port.closed
    assert connection.port is port


def test_account_capability_and_source_request():
    connection = Connection("/dev/null")
    connection.port = FakePort(b"@USAGE_HELLO 1\n@API_SOURCE official\n@API_SOURCE bogus\n")
    assert connection.receive() == 0
    assert connection.account_protocol
    assert connection.api_mode_request == "official"


def test_rotation_capability_request_and_deduplicated_sync():
    from protocol import rotation_packet

    class Writer(FakePort):
        def __init__(self, data):
            super().__init__(data)
            self.writes = []

        def write(self, data):
            self.writes.append(data)
            return len(data)

    connection = Connection("/dev/null")
    connection.port = Writer(b"@ROTATE_HELLO 1\n@ROTATE_MODE all\n@ROTATE_MODE bogus\n")
    connection.receive()
    assert connection.rotation_supported
    assert connection.rotation_mode_request == "all"
    connection.send_rotation_mode("active", 10)
    connection.send_rotation_mode("active", 11)
    assert connection.port.writes == [rotation_packet(1, "active")]
    connection.send_rotation_mode("off", 12)
    assert connection.port.writes[-1] == rotation_packet(2, "off")


def test_legacy_firmware_receives_original_packet_size():
    from protocol import packet, LABEL_BYTES

    class Writer:
        def write(self, data):
            self.data = data
            return len(data)

    connection = Connection("/dev/null")
    connection.port = Writer()
    view = dict(state="thinking", labels=bytes(LABEL_BYTES), api_source="official",
                api_mode="official", week_remaining_x10=900, account_kind=1,
                week_reset="09-29 11:29", plan_name="Pro")
    connection.send(view)
    original_size = len(packet(1, state="thinking", labels=bytes(LABEL_BYTES)))
    assert len(connection.port.data) == original_size
    connection.account_protocol = True
    connection.send(view)
    assert len(connection.port.data) == original_size + 41
    connection.port.data = b""
    connection.last_payload = None
    view["membership_expires_at"] = "2026-10-22T23:59"
    connection.send(view)
    assert len(connection.port.data) == original_size + 41
    connection.expiry_protocol = True
    connection.send(view)
    assert len(connection.port.data) == original_size + 58
