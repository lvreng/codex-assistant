import binascii
import struct
import time

import pytest

from bridge import Connection
from desktop_ipc import DesktopClient, DesktopHub, TEST_STATUS
from protocol import MAGIC, model_test_packet


class Port:
    def __init__(self, data=b""):
        self.data = data
        self.writes = []

    @property
    def in_waiting(self):
        return len(self.data)

    def read(self, size):
        result, self.data = self.data[:size], self.data[size:]
        return result

    def write(self, data):
        self.writes.append(data)
        return len(data)


def test_control_packet_and_capability_gate():
    for start in (False, True):
        data = model_test_packet(65537, start)
        assert data[:4] == MAGIC
        assert struct.unpack_from("<HHBB", data, 4) == (2, 1, 6, int(start))
        assert int.from_bytes(data[-2:], "little") == binascii.crc_hqx(data[4:-2], 65535)
    with pytest.raises(ValueError):
        model_test_packet(1, "start")
    connection = Connection("unused")
    assert not connection.request_model_test(True)
    connection.port = Port(b"@MODEL_TEST_HELLO 1\n")
    assert not connection.request_model_test(True)
    connection.receive()
    assert connection.request_model_test(True)
    assert connection.port.writes == [model_test_packet(1, True)]
    assert 1 in connection.awaiting


def test_device_status_and_errors_do_not_change_real_model():
    connection = Connection("unused")
    connection.last_model = 4
    connection.port = Port(b"@MODEL_TEST active=1 step=2 model=2\n")
    connection.receive()
    assert connection.model_test_status == dict(active=True, step=2, model=2, error="")
    assert connection.last_model == 4
    connection.port.data = b"@MODEL_TEST_ERROR no_sd\n@MODEL_TEST active=0 step=0 model=0\n"
    connection.receive()
    assert connection.model_test_status["error"] == "no_sd"
    assert not connection.model_test_status["active"]


def test_ipc_controls_require_a_live_subscriber_and_status_is_separate(tmp_path):
    hub = DesktopHub(tmp_path)
    client = DesktopClient(tmp_path)
    try:
        client.sock.sendto(b"MODEL_TEST 1", str(hub.socket_path))
        hub.poll()
        assert hub.model_test_request is None
        assert client.request_model_test(True)
        hub.poll()
        assert hub.model_test_request is True
        assert client.request_model_test(False)
        hub.poll()
        assert hub.model_test_request is False
        status = dict(available=True, active=True, step=3, model=3, error="")
        hub.publish_test_status(status)
        hub.publish(b"real-packet", model="gpt-6-astra")
        assert client.receive() == [b"real-packet"]
        assert client.model == "gpt-6-astra"
        assert client.model_test_status == status
        assert time.monotonic()-client.model_test_updated < 1
        hub._send(TEST_STATUS+b'{"active":true}', str(client.path))
        assert client.receive() == []
        assert client.model_test_status == status
    finally:
        client.close()
        hub.close()
