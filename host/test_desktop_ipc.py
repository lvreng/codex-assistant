import stat
from pathlib import Path

import pytest

from desktop_ipc import DesktopClient, DesktopHub, decode_view, encode_view, runtime_dir


class FakeDatagramSocket:
    def __init__(self, incoming=()):
        self.incoming = list(incoming)
        self.sent = []

    def recvfrom(self, _size):
        if not self.incoming:
            raise BlockingIOError
        return self.incoming.pop(0)

    def sendto(self, data, address):
        self.sent.append((data, address))


def test_rotation_status_and_request_without_socket_binding():
    client = DesktopClient.__new__(DesktopClient)
    client.bridge = Path("/runtime/bridge.sock")
    client.sock = FakeDatagramSocket([
        (b"ROTATION_STATUS all", str(client.bridge)),
        (b"ROTATION_STATUS invalid", str(client.bridge)),
    ])
    client.model = ""
    client.rotation_mode = "active"
    client.model_test_status = {"available": False, "active": False, "step": 0,
                                "model": 0, "error": ""}
    client.model_test_updated = 0
    client._last_subscribe = 0
    assert client.receive() == []
    assert client.rotation_mode == "all"
    client.subscribe = lambda *_args, **_kwargs: True
    assert client.set_rotation_mode("off")
    assert client.sock.sent == [(b"ROTATION off", str(client.bridge))]


def test_model_and_packet_are_atomic_and_cached(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    hub.publish(b"sol", now=0, model="gpt-5.6-sol")
    client.subscribe(now=0)
    hub.poll(now=0)
    assert client.receive() == [b"sol"]
    assert client.model == "gpt-5.6-sol"
    hub.publish(b"astra", now=0, model="gpt-6-astra")
    assert client.receive() == [b"astra"]
    assert client.model == "gpt-6-astra"
    hub.publish(b"old-bridge", now=0)
    assert client.receive() == [b"old-bridge"]
    assert client.model == ""
    client.close()
    hub.close()


def test_envelope_validation_and_model_length():
    assert decode_view(encode_view(b"frame", "x" * 300)) == (b"frame", "x" * 160)
    for value in (b"CDXM\x01", b"CDXM\x01\xff\xff", b"CDXM\x01\x02\x00[]frame"):
        with pytest.raises(ValueError):
            decode_view(value)


def test_packets_and_cached_reconnect(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    client.subscribe(now=0)
    assert hub.poll(now=0) == 0
    hub.publish(b"state", now=0)
    assert client.receive() == [b"state"]
    hub.sock.close()
    hub._lock.close()
    hub = DesktopHub(tmp_path)
    client.subscribe(now=1)
    assert hub.poll(now=1) == 0
    hub.publish(b"new", now=1)
    assert client.receive() == [b"new"]
    client.close()
    hub.close()


def test_cached_packet_is_sent_immediately_on_subscribe(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    hub.publish(b"cached", now=0)
    client.subscribe(now=0)
    assert hub.poll(now=0) == 0
    assert client.receive() == [b"cached"]
    client.close()
    hub.close()


def test_navigation_bounds_and_invalid_commands(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    client.subscribe(now=0)
    assert hub.poll(now=0) == 0
    assert client.navigate(1)
    assert client.navigate(-1)
    assert not client.navigate(2)
    assert hub.poll(now=0) == 0
    client.sock.sendto(b"NAV nope", str(hub.socket_path))
    client.sock.sendto(b"UNSUB", str(hub.socket_path))
    assert hub.poll(now=0) == 0
    client.close()
    hub.close()


def test_rotation_mode_round_trip_and_cached_status(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    client.subscribe(now=0)
    hub.poll(now=0)
    hub.publish_rotation_mode("active")
    client.receive()
    assert client.rotation_mode == "active"
    assert client.set_rotation_mode("all")
    hub.poll(now=1)
    assert hub.rotation_mode_request == "all"
    hub.publish_rotation_mode("all")
    client.receive()
    assert client.rotation_mode == "all"
    second = DesktopClient(tmp_path)
    second.subscribe(now=2)
    hub.poll(now=2)
    second.receive()
    assert second.rotation_mode == "all"
    with pytest.raises(ValueError):
        client.set_rotation_mode("sometimes")
    with pytest.raises(ValueError):
        hub.publish_rotation_mode("sometimes")
    client.close()
    second.close()
    hub.close()


def test_duplicate_hub_and_expiry(tmp_path):
    first = DesktopHub(tmp_path)
    with pytest.raises(RuntimeError):
        DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    client.subscribe(now=0)
    first.poll(now=0)
    assert client.navigate(1)
    assert first.poll(now=0) == 1
    assert first.poll(now=6) == 0
    client.close()
    first.close()


def test_existing_peer_refreshes_at_full_capacity_and_expired_nav_is_rejected(tmp_path):
    hub = DesktopHub(tmp_path)
    clients = [DesktopClient(tmp_path) for _ in range(8)]
    for client in clients:
        client.subscribe(now=0)
    assert hub.poll(now=0) == 0
    clients[0].subscribe(now=1)
    assert hub.poll(now=1) == 0
    assert hub.peers[str(clients[0].path)] == 1
    assert len(hub.peers) == 8
    assert clients[0].navigate(1)
    assert hub.poll(now=6) == 0
    for client in clients:
        client.close()
    hub.close()


def test_outside_peer_rejected_and_gone_peer_dropped(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    outside = DesktopClient(tmp_path.parent)
    outside.sock.sendto(b"SUB", str(hub.socket_path))
    assert hub.poll(now=0) == 0
    assert not hub.peers
    client.subscribe(now=0)
    assert hub.poll(now=0) == 0
    client.sock.close()
    client.path.unlink()
    hub.publish(b"gone", now=0)
    assert str(client.path) not in hub.peers
    outside.close()
    hub.close()


def test_permissions_and_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    directory = runtime_dir()
    assert directory.name == "codex-pet"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    hub = DesktopHub(directory)
    assert stat.S_IMODE(hub.socket_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(hub.lock_path.stat().st_mode) == 0o600
    hub.close()
