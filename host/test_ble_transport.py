import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from ble_transport import BleSerial, NUS_SERVICE_UUID, NUS_RX_UUID


class FakeClient:
    def __init__(self, mtu_size=247, write_delay=0, max_write=None):
        self.writes = []
        self.callback = None
        self.disconnected = threading.Event()
        self.mtu_size = mtu_size
        self.max_write = max_write
        self.write_delay = write_delay
        self.pause_writes = False
        self.write_started = threading.Event()
        self.services = [SimpleNamespace(uuid=NUS_SERVICE_UUID, characteristics=[
            SimpleNamespace(uuid="6e400002-b5a3-f393-e0a9-e50e24dcca9e", max_write_without_response_size=max_write),
            SimpleNamespace(uuid="6e400003-b5a3-f393-e0a9-e50e24dcca9e")])]

    async def connect(self): pass
    def set_disconnected_callback(self, callback): self.disconnect_callback = callback
    async def start_notify(self, _uuid, callback): self.callback = callback
    async def stop_notify(self, _uuid): pass
    async def disconnect(self): self.disconnected.set()
    async def write_gatt_char(self, uuid, data, response=False):
        if self.pause_writes:
            self.write_started.set()
            while self.pause_writes:
                await asyncio.sleep(.001)
        if self.write_delay:
            await asyncio.sleep(self.write_delay)
        self.writes.append((uuid, bytes(data), response))


class FakeBackend:
    def __init__(self, devices, mtu_size=247, max_write=None):
        self.devices, self.client = devices, FakeClient(mtu_size, max_write=max_write)
        self.BleakScanner = SimpleNamespace(discover=self.discover)
        self.BleakClient = self.client_factory
    async def discover(self, service_uuids, return_adv=False):
        assert service_uuids == [NUS_SERVICE_UUID]
        return {d.address: (d, SimpleNamespace(service_uuids=d.service_uuids)) for d in self.devices}
    def client_factory(self, device, disconnected_callback, pair):
        self.client.disconnect_callback = disconnected_callback
        return self.client


def wait_ready(s):
    deadline = time.time() + 2
    while time.time() < deadline and not s.ready: time.sleep(.01)
    assert s.ready


def device(address):
    return SimpleNamespace(address=address, service_uuids=[NUS_SERVICE_UUID])


def test_fragmentation_order_and_rx():
    b = FakeBackend([device("a")]); s = BleSerial(backend=b); s.open(); wait_ready(s)
    payload = bytes(range(256)) * 2; assert s.write(payload) == len(payload)
    deadline = time.time() + 2
    while len(b.client.writes) < 3 and time.time() < deadline: time.sleep(.01)
    assert b.client.writes == [(NUS_RX_UUID, payload[:244], True), (NUS_RX_UUID, payload[244:488], True), (NUS_RX_UUID, payload[488:], True)]
    b.client.callback(None, b"ab"); b.client.callback(None, b"cd"); assert s.read(3) == b"abc"; assert s.in_waiting == 1
    s.close()


def test_mtu_fragment_sizes():
    for mtu, expected in ((23, 20), (247, 244)):
        b = FakeBackend([device("a")], mtu_size=mtu); s = BleSerial(backend=b); s.open(); wait_ready(s)
        payload = bytes(range(256))
        s.write(payload)
        deadline = time.time() + 2
        while sum(len(item[1]) for item in b.client.writes) < len(payload) and time.time() < deadline:
            time.sleep(.01)
        assert max(len(item[1]) for item in b.client.writes) == expected
        s.close()


def test_bluez_mtu_property_uses_characteristic_limit():
    b = FakeBackend([device("a")], mtu_size=23, max_write=244)
    s = BleSerial(backend=b); s.open(); wait_ready(s)
    payload = bytes(range(256)); s.write(payload)
    deadline = time.time() + 2
    while sum(len(item[1]) for item in b.client.writes) < len(payload) and time.time() < deadline:
        time.sleep(.01)
    assert max(len(item[1]) for item in b.client.writes) == 244
    s.close()


def test_acquired_mtu_overrides_a_stale_characteristic_cache():
    b = FakeBackend([device("a")], mtu_size=23, max_write=20)
    s = BleSerial(backend=b); s.open(); wait_ready(s)
    s._mtu_payload = 244
    s.write(bytes(range(256)))
    assert s.flush(.5)
    assert [len(item[1]) for item in b.client.writes] == [244, 12]
    s.close()


def test_registered_board_scan_returns_on_exact_service_and_address():
    b = FakeBackend([device("a")])

    async def find(predicate, timeout, service_uuids):
        adv = SimpleNamespace(service_uuids=[NUS_SERVICE_UUID])
        assert not predicate(device("b"), adv)
        assert not predicate(device("a"), SimpleNamespace(service_uuids=[]))
        assert predicate(device("a"), adv)
        return device("a")

    b.BleakScanner.find_device_by_filter = find
    s = BleSerial("a", backend=b)
    s.open(); wait_ready(s); s.close()


def test_flush_waits_for_inflight_and_has_bound():
    b = FakeBackend([device("a")]); s = BleSerial(backend=b); s.open(); wait_ready(s)
    s.write(b"goodbye")
    assert s.flush(timeout=.5)
    s.close()

    b = FakeBackend([device("a")]); b.client.write_delay = .2
    s = BleSerial(backend=b); s.open(); wait_ready(s); s.write(b"slow")
    start = time.time()
    assert not s.flush(timeout=.01)
    assert time.time() - start < .15
    s.close()


def test_multiple_matches_and_backpressure():
    s = BleSerial(backend=FakeBackend([device("a"), device("b")])); s.open()
    deadline = time.time() + 2
    while not s._error and time.time() < deadline: time.sleep(.01)
    with pytest.raises(OSError): s.in_waiting
    s.close()
    b = FakeBackend([device("a")]); s = BleSerial(backend=b); s.open(); wait_ready(s)
    b.client.pause_writes = True
    s.write(b"inflight")
    assert b.client.write_started.wait(1)
    for _ in range(31): s.write(b"x")
    with pytest.raises(BlockingIOError): s.write(b"x")
    b.client.pause_writes = False
    s.close()


def test_open_and_close_are_nonblocking():
    class Slow(FakeBackend):
        async def discover(self, service_uuids, return_adv=False): await asyncio.sleep(1); return []
    s = BleSerial(backend=Slow([])); start = time.time(); s.open(); assert time.time() - start < .2
    start = time.time(); s.close(); assert time.time() - start < .2
    assert s.wait_closed(timeout=1)


def test_real_return_adv_signature_and_disconnect_error():
    class Advertisement:
        service_uuids = [NUS_SERVICE_UUID]

    class ScannerBackend(FakeBackend):
        async def discover(self, service_uuids, return_adv=False):
            return [(device("a"), Advertisement())]

    b = ScannerBackend([]); s = BleSerial(backend=b); s.open(); wait_ready(s)
    b.client.disconnect_callback(b.client)
    with pytest.raises(OSError, match="disconnected"):
        _ = s.ready
    s.close()


def test_close_cancels_discovery_and_connect():
    class Slow(FakeBackend):
        async def discover(self, service_uuids, return_adv=False): await asyncio.sleep(10); return []
    s = BleSerial(backend=Slow([])); s.open(); time.sleep(.02)
    start = time.time(); s.close(); assert time.time() - start < .2
    assert s.wait_closed(1)

    b = FakeBackend([device("a")])
    began = threading.Event()

    async def connect():
        began.set()
        await asyncio.sleep(10)

    b.client.connect = connect
    s = BleSerial(backend=b); s.open()
    assert began.wait(1)
    start = time.time(); s.close(); assert time.time() - start < .2
    assert s.wait_closed(1)


def test_close_after_error_does_not_cancel_disconnect_cleanup():
    b = FakeBackend([device("a")])
    b.client.services = []
    began = threading.Event()

    async def disconnect():
        began.set()
        await asyncio.sleep(.05)
        b.client.disconnected.set()

    b.client.disconnect = disconnect
    s = BleSerial(backend=b); s.open()
    assert began.wait(1)
    s.close()
    assert s.wait_closed(1) and b.client.disconnected.is_set()
