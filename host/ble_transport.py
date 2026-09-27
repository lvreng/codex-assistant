"""Small, non-blocking BLE NUS serial adapter."""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from collections import deque

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
_MAX_BUFFER = 128 * 1024
_MAX_PACKETS = 32


class BleSerial:
    def __init__(self, address=None, *, pair=True, connect_timeout=20, backend=None):
        self.address = address
        self.pair = pair
        self.connect_timeout = connect_timeout
        self.backend = backend
        self._in = bytearray()
        self._out = deque()
        self._out_bytes = 0
        self._inflight = 0
        self._drained = threading.Event()
        self._drained.set()
        self._lock = threading.Lock()
        self._wake = None
        self._thread = None
        self._loop = None
        self._client = None
        self._closed = True
        self._ready = False
        self._error = None
        self._stop = None
        self._main_task = None
        self._writer_task = None
        self._cleaning = False
        self._rx_characteristic = None
        self._mtu_payload = 20

    @property
    def ready(self):
        self._raise_error()
        with self._lock:
            return self._ready

    @property
    def selected_address(self):
        return self.address

    @property
    def pending_bytes(self):
        with self._lock:
            return self._out_bytes

    @property
    def mtu_payload(self):
        return self._mtu_payload

    @property
    def in_waiting(self):
        self._raise_error()
        with self._lock:
            return len(self._in)

    def open(self):
        with self._lock:
            if not self._closed:
                return
            self._closed = False
            self._ready = False
            self._error = None
            self._in.clear()
            self._out.clear()
            self._out_bytes = 0
            self._inflight = 0
            self._drained.set()
            self._client = None
            self._writer_task = None
            self._cleaning = False
            self._rx_characteristic = None
            self._mtu_payload = 20
        self._thread = threading.Thread(target=self._run, name="ble-serial", daemon=True)
        self._thread.start()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._ready = False
        if self._loop and self._stop:
            try:
                self._loop.call_soon_threadsafe(self._cancel_main)
            except RuntimeError:
                pass  # The worker completed between checking and scheduling.

    def wait_closed(self, timeout=3):
        """Drain asynchronous disconnection on process exit, not on UI ticks."""
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def write(self, data):
        data = bytes(data)
        self._raise_error()
        with self._lock:
            if self._closed:
                raise OSError("BLE adapter is closed")
            if len(self._out) + self._inflight >= _MAX_PACKETS or self._out_bytes + len(data) > _MAX_BUFFER:
                raise BlockingIOError("BLE transmit queue is full")
            self._out.append(data)
            self._out_bytes += len(data)
            self._drained.clear()
        loop = self._loop
        wake = self._wake
        if loop and wake:
            try:
                loop.call_soon_threadsafe(wake.set)
            except RuntimeError as exc:
                raise OSError("BLE worker stopped") from exc
        return len(data)

    def flush(self, timeout=0.5):
        """Wait up to ``timeout`` seconds for queued writes to complete."""
        if timeout < 0:
            raise ValueError("negative flush timeout")
        self._raise_error()
        if not self.ready:
            return False
        deadline = time.monotonic() + timeout
        while True:
            self._raise_error()
            with self._lock:
                if not self._out and self._inflight == 0:
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._drained.wait(remaining)

    def read(self, n):
        self._raise_error()
        if n < 0:
            raise ValueError("negative read size")
        with self._lock:
            result = bytes(self._in[:n])
            del self._in[:n]
            return result

    def _raise_error(self):
        with self._lock:
            if self._error is not None:
                raise OSError(f"BLE adapter failed: {self._error}") from self._error

    def _run(self):
        with self._lock:
            if self._closed:
                return
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        try:
            self._main_task = loop.create_task(self._main())
            loop.run_until_complete(self._main_task)
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None
            self._main_task = None

    def _cancel_main(self):
        self._stop.set()
        if self._main_task and not self._main_task.done() and not self._cleaning:
            self._main_task.cancel()

    async def _main(self):
        try:
            with self._lock:
                if self._closed:
                    return
            backend = self.backend or importlib.import_module("bleak")
            device = await self._discover(backend)
            if device is None:
                raise OSError("no BLE NUS device found")
            with self._lock:
                if self._closed:
                    return
            self.address = getattr(device, "address", device if isinstance(device, str) else self.address)
            client = await self._connect(backend, device)
            self._client = client
            self._rx_characteristic = self._validate_services(client)
            await client.start_notify(NUS_TX_UUID, self._notification)
            with self._lock:
                if not self._closed:
                    self._ready = True
                if self._out:
                    self._wake.set()
            self._writer_task = asyncio.create_task(self._writer())
            await self._writer_task
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            with self._lock:
                if not self._closed:
                    self._error = exc
                self._ready = False
        finally:
            self._cleaning = True
            with self._lock:
                self._ready = False
            client = self._client
            if client is not None:
                try:
                    await client.stop_notify(NUS_TX_UUID)
                except Exception:
                    pass
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _discover(self, backend):
        if self.address and self.backend is None and sys.platform == "linux":
            from bluez_pairing import connected_board
            cached = await connected_board(self.address)
            if cached is not None:
                return cached
        scanner = backend.BleakScanner
        find = getattr(scanner, "find_device_by_filter", None)
        if self.address and find is not None:
            device = await find(
                lambda device, adv: str(device.address).lower() == str(self.address).lower()
                and self._has_service(device, adv),
                timeout=self.connect_timeout, service_uuids=[NUS_SERVICE_UUID],
            )
            if device is None:
                raise OSError("registered BLE board is not advertising NUS")
            return device
        found = await scanner.discover(service_uuids=[NUS_SERVICE_UUID], return_adv=True)
        matches = [(device, adv) for device, adv in self._scan_items(found)
                   if self._has_service(device, adv)]
        if self.address:
            for device, _adv in matches:
                device_address = getattr(device, "address", None)
                if str(device_address).lower() == str(self.address).lower():
                    return device
            raise OSError("requested BLE device does not provide NUS")
        if len(matches) != 1:
            if len(matches) > 1:
                raise OSError("multiple BLE NUS devices found")
            return None
        return matches[0][0]

    @staticmethod
    def _scan_items(found):
        if isinstance(found, dict):
            return found.values()
        return ((item[0], item[1]) if isinstance(item, tuple) and len(item) == 2 else (item, None)
                for item in found)

    @staticmethod
    def _has_service(device, advertisement=None):
        uuids = getattr(advertisement, "service_uuids", None)
        if uuids is None:
            uuids = getattr(device, "service_uuids", None)
        if uuids is None and isinstance(device, dict):
            uuids = device.get("service_uuids")
        return any(str(u).lower() == NUS_SERVICE_UUID for u in (uuids or ()))

    async def _connect(self, backend, device):
        if self.pair and self.backend is None and sys.platform == "linux":
            from bluez_pairing import pair_board
            await pair_board(device, timeout=self.connect_timeout)
        client = backend.BleakClient(
            device, disconnected_callback=self._disconnected, pair=self.pair,
        )
        self._client = client
        await asyncio.wait_for(client.connect(), self.connect_timeout)
        if self.backend is None and sys.platform == "linux":
            # Bleak's documented BlueZ workaround obtains the live ATT MTU via
            # AcquireWrite and immediately releases its fd. Cached GATT objects
            # can otherwise keep reporting 20-byte writes after reconnecting.
            acquire_mtu = getattr(client._backend, "_acquire_mtu", None)
            if acquire_mtu is not None:
                await asyncio.wait_for(acquire_mtu(), self.connect_timeout)
                self._mtu_payload = min(244, max(20, client.mtu_size - 3))
        return client

    @staticmethod
    def _validate_services(client):
        services = getattr(client, "services", None)
        if services is None:
            raise OSError("BLE backend did not expose GATT services")
        uuids = {str(getattr(service, "uuid", service)).lower() for service in services}
        if NUS_SERVICE_UUID not in uuids:
            raise OSError("connected BLE device does not expose NUS service")
        rx = None
        tx = None
        get_characteristic = getattr(services, "get_characteristic", None)
        if get_characteristic:
            rx = get_characteristic(NUS_RX_UUID)
            tx = get_characteristic(NUS_TX_UUID)
        if rx is None or tx is None:
            for service in services:
                for characteristic in getattr(service, "characteristics", ()):
                    uuid = str(getattr(characteristic, "uuid", characteristic)).lower()
                    if uuid == NUS_RX_UUID:
                        rx = characteristic
                    elif uuid == NUS_TX_UUID:
                        tx = characteristic
        if rx is None or tx is None:
            raise OSError("connected BLE device lacks NUS RX/TX characteristics")
        return rx

    async def _writer(self):
        while not self._stop.is_set():
            await self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            while True:
                with self._lock:
                    if not self._out:
                        break
                    packet = self._out.popleft()
                    self._inflight += 1
                candidates = [self._mtu_payload]
                max_write = getattr(self._rx_characteristic, "max_write_without_response_size", None)
                if max_write is not None:
                    candidates.append(int(max_write))
                # BlueZ's generic mtu_size is always 23 and emits a warning;
                # its characteristic limit reflects the negotiated ATT MTU.
                if not (self.backend is None and sys.platform == "linux" and max_write is not None):
                    mtu_size = getattr(self._client, "mtu_size", None)
                    if mtu_size is not None:
                        candidates.append(max(20, int(mtu_size) - 3))
                size = min(244, max(candidates))
                try:
                    for offset in range(0, len(packet), size):
                        await self._client.write_gatt_char(NUS_RX_UUID, packet[offset:offset + size], response=True)
                finally:
                    with self._lock:
                        self._inflight -= 1
                        self._out_bytes -= len(packet)
                        if not self._out and self._inflight == 0:
                            self._drained.set()

    def _notification(self, _sender, data):
        if self._closed:
            return
        with self._lock:
            if len(self._in) + len(data) > _MAX_BUFFER:
                self._error = BufferError("BLE receive queue is full")
                if self._writer_task:
                    self._writer_task.cancel()
                return
            self._in.extend(data)

    def _disconnected(self, _client=None):
        if self._closed:
            return
        with self._lock:
            self._error = OSError("BLE backend disconnected")
            self._ready = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)
            if self._writer_task:
                self._loop.call_soon_threadsafe(self._writer_task.cancel)
