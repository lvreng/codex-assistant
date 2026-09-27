#!/usr/bin/env python3
"""Codex -> USB/BLE -> ESP32 state and account telemetry; artwork stays on SD."""
from __future__ import annotations
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import signal
import sys
import time
import serial
from ble_transport import BleSerial
from codex_state import Chooser, Detector, Session, STATES
from protocol import (ACCOUNT_KEYS, disconnect_packet, model_packet, model_test_packet,
                      model_picker_packet, packet, rotation_packet)
from model_control import ModelControl
from text_labels import labels
from settings import Settings
from official_usage import UsageRouter
from desktop_ipc import DesktopHub

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = "auto"


def resolve_port(path):
    if path != "auto":
        return path
    devices = sorted(Path("/dev/serial/by-id").glob("usb-Espressif_USB_JTAG_serial_debug_unit_*-if00"))
    if len(devices) == 1:
        return str(devices[0])
    if len(devices) > 1:
        raise RuntimeError("Multiple ESP32 USB devices; select one with --port")
    raise RuntimeError("Waiting for an ESP32 USB device")


class Connection:
    def __init__(self, port, *, transport="usb", bluetooth_address=None, peer_file=None):
        self.path, self.port = port, None
        self.transport_mode = transport
        self.transport = "usb"
        self.peer_file = peer_file or Path.home()/".config/codex-pet/bluetooth.json"
        self.bluetooth_address = bluetooth_address
        if not self.bluetooth_address and transport != "usb":
            try:
                candidate = json.loads(self.peer_file.read_text()).get("address", "")
                if re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", candidate):
                    self.bluetooth_address = candidate
            except (OSError, ValueError, AttributeError, TypeError):
                pass
        self._pairing = None
        self._pairing_closing = False
        self._pairing_done = False
        self._next_pairing = 0.0
        self._usb_probe = None
        self._next_usb_probe = 0.0
        self._usb_unhealthy_until = 0.0
        self._last_hello = b""
        self.pending = b""
        self.last_ack = 0.0
        self.next_retry = 0.0
        self.sequence = 0
        self.last_payload = None
        self.last_model = None
        self.sent = 0
        self.awaiting = {}
        self._opening = None
        self._hello_buffer = b""
        self._hello_deadline = 0.0
        self._next_probe = 0.0
        self.art_request = None
        self.art_ack = None
        self.art_ack_status = None
        self.art_stats = {}
        self.sd_media = False
        self.account_protocol = False
        self.expiry_protocol = False
        self.api_mode_request = None
        self.rotation_mode_request = None
        self.rotation_supported = False
        self.last_rotation_mode = None
        self.model_test_supported = False
        self.model_picker_supported = False
        self.model_picker_requests = []
        self.last_picker_payload = None
        self.last_picker_send = 0.0
        self.model_test_status = {"active": False, "step": 0, "model": 0, "error": ""}

    def _begin_usb(self):
        port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=1, exclusive=True)
        # ESP32-P4's native USB Serial/JTAG treats deasserting both CDC
        # control lines as a chip reset request. Keep both asserted before
        # opening the device so reconnects never reset the display firmware.
        port.dtr = True
        port.rts = True
        port.port = resolve_port(self.path)
        try:
            port.open()
            port.reset_input_buffer()
            self._opening = port
            self.transport = "usb"
            self._hello_buffer = b""
            self._hello_deadline = time.monotonic() + 7
            self._next_probe = 0.0
        except BaseException:
            port.close()
            raise

    def begin_open(self):
        if self.transport_mode != "ble" and (self.transport_mode == "usb" or
                                            time.monotonic() >= self._usb_unhealthy_until):
            try:
                self._begin_usb()
                return
            except (OSError, RuntimeError) as exc:
                if self.transport_mode == "usb" or "Multiple" in str(exc):
                    raise
        if not self.bluetooth_address:
            raise RuntimeError("USB unavailable; connect USB once to register and pair this board's Bluetooth")
        port = BleSerial(self.bluetooth_address)
        port.open()
        self._opening = port
        self.transport = "ble"
        self._hello_buffer = b""
        self._hello_deadline = time.monotonic() + 30
        self._next_probe = 0.0

    def poll_open(self):
        """Advance the handshake without blocking desktop telemetry on USB."""
        if self._opening is None:
            return self.port is not None
        now = time.monotonic()
        if now >= self._hello_deadline:
            if self.transport == "usb":
                self._usb_unhealthy_until = now + 15
            self.close()
            raise RuntimeError("No pet handshake; refusing to send binary data to another firmware")
        port = self._opening
        if self.transport == "ble" and not port.ready:
            return False
        if now >= self._next_probe:
            # Never send binary packets to a device without a pet handshake.
            port.write(b"?")
            self._next_probe = now + 1
        waiting = port.in_waiting
        if waiting:
            self._hello_buffer += port.read(min(4096, waiting))
        if re.search(rb"@PET_HELLO 2\r?\n", self._hello_buffer):
            self._last_hello = self._hello_buffer
            self.port, self._opening = port, None
            self.last_ack = now
            self.last_payload = None
            self.pending = b""
            self.awaiting.clear()
            self.expiry_protocol = b"@USAGE_HELLO 2" in self._hello_buffer
            self.account_protocol = self.expiry_protocol or b"@USAGE_HELLO 1" in self._hello_buffer
            self.model_test_supported = b"@MODEL_TEST_HELLO 1" in self._hello_buffer
            self.model_picker_supported = b"@MODEL_PICK_HELLO 1" in self._hello_buffer
            self.rotation_supported = b"@ROTATE_HELLO 1" in self._hello_buffer
            self._hello_buffer = b""
            self.last_model = None
            self.last_rotation_mode = None
            self._learn_ble(self._last_hello)
            print(f"[{self.transport.upper()}] 表情固件已确认，开始同步本地会话", flush=True)
            if self.transport == "ble":
                self._pairing_done = True
                print(f"[BLE] ATT payload={getattr(port, 'mtu_payload', 20)} bytes", flush=True)
            return True
        self._hello_buffer = self._hello_buffer[-8192:]
        return False

    def open(self):
        """Synchronous entry point retained for diagnostics."""
        self.begin_open()
        try:
            while not self.poll_open():
                time.sleep(0.02)
        except BaseException:
            self.close()
            raise

    def close(self, *, wait=False):
        closing = [port for port in (self._pairing, self.port, self._opening) if port is not None]
        if self._pairing is not None:
            self._pairing.close()
            self._pairing = None
        self._pairing_closing = False
        if self._usb_probe is not None:
            self._usb_probe.close(wait=wait)
            self._usb_probe = None
        if self.port is not None:
            self.port.close()
        self.port = None
        if self._opening is not None:
            self._opening.close()
        self._opening = None
        if wait:
            for port in closing:
                wait_closed = getattr(port, "wait_closed", None)
                if wait_closed:
                    wait_closed(timeout=3)
        self.last_payload = None
        self.last_model = None
        self.awaiting.clear()
        self.art_request = None
        self.art_ack = None
        self.art_ack_status = None
        self.art_stats = {}
        self.sd_media = False
        self.account_protocol = False
        self.expiry_protocol = False
        self.rotation_supported = False
        self.last_rotation_mode = None
        self.model_test_supported = False
        self.model_picker_supported = False
        self.model_picker_requests.clear()
        self.last_picker_payload = None
        self.model_test_status = {"active": False, "step": 0, "model": 0, "error": "disconnected"}

    def _learn_ble(self, data):
        if self.transport != "usb" or self.transport_mode == "usb":
            return
        match = re.search(rb"(?:^|\n)@BLE_ID ((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\b", data)
        if match:
            address = match[1].decode("ascii").upper()
            if address != self.bluetooth_address:
                self.bluetooth_address = address
                self._pairing_done = False

    def retry_after_failure(self):
        now = time.monotonic()
        if self.transport == "usb":
            self._usb_unhealthy_until = now + 15
        self.close()
        self.next_retry = now + 2

    def poll_transport(self, now):
        """Pair over the known USB identity, and probe USB without dropping BLE."""
        if self.transport_mode != "auto" or self.port is None:
            return
        if self.transport == "usb":
            if not self.bluetooth_address or self._pairing_done or now < self._next_pairing:
                return
            try:
                if self._pairing is None:
                    self._pairing = BleSerial(self.bluetooth_address)
                    self._pairing.open()
                    self._next_pairing = 0
                elif self._pairing_closing:
                    if self._pairing.wait_closed(timeout=0):
                        self._pairing = None
                        self._pairing_closing = False
                        self._pairing_done = True
                        print(f"[BLE] 已配对 {self.bluetooth_address}，USB 断开后自动接管", flush=True)
                elif self._pairing.ready:
                    self.peer_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    temporary = self.peer_file.with_suffix(".tmp")
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "w") as stream:
                        json.dump({"address": self.bluetooth_address, "source": "usb-handshake"}, stream)
                    os.replace(temporary, self.peer_file)
                    self._pairing.close()
                    self._pairing_closing = True
            except (OSError, RuntimeError) as exc:
                if self._pairing is not None:
                    self._pairing.close()
                    self._pairing = None
                self._pairing_closing = False
                self._next_pairing = now + 30
                print(f"[BLE] 配对暂未完成，USB 通讯保持正常：{exc}", flush=True)
            return
        if self._usb_probe is None and now >= max(self._next_usb_probe, self._usb_unhealthy_until):
            self._next_usb_probe = now + 3
            probe = Connection(self.path)
            try:
                probe.begin_open()
                self._usb_probe = probe
            except (OSError, RuntimeError):
                probe.close()
        if self._usb_probe is not None:
            try:
                if self._usb_probe.poll_open():
                    probe, self._usb_probe = self._usb_probe, None
                    port, probe.port = probe.port, None
                    hello = probe._last_hello
                    self.close()
                    self.transport = "usb"
                    self._opening = port
                    self._hello_buffer = hello
                    self._hello_deadline = now + 7
                    self._next_probe = now + 1
                    self.poll_open()
                    print("[LINK] USB 已恢复，已从蓝牙切回 USB", flush=True)
            except (OSError, RuntimeError):
                if self._usb_probe is not None:
                    self._usb_probe.close()
                    self._usb_probe = None
                self._next_usb_probe = now + 10

    def receive(self):
        nav_delta = 0
        self.pending += self.port.read(min(4096,self.port.in_waiting))
        while b"\n" in self.pending:
            line,self.pending = self.pending.split(b"\n",1)
            self._learn_ble(line)
            if line.startswith(b"@ACK "):
                match = re.match(rb"@ACK (\d+)\b",line)
                sequence = int(match[1]) if match else -1
                if sequence in self.awaiting:
                    self.last_ack = time.monotonic()
                    del self.awaiting[sequence]
            elif line.startswith(b"@STAT "):
                print(line.decode("ascii",errors="replace").strip(),flush=True)
            elif line.startswith(b"@NAV "):
                match = re.match(rb"@NAV (-?1)\b", line)
                if match:
                    nav_delta += int(match[1])
            elif line.startswith(b"@ART_REQUEST "):
                match = re.fullmatch(rb"@ART_REQUEST (\d+) (\d+) (\d+) (\d+) (\d+) (\d+)\r?", line)
                if match:
                    gen, theme, preview, style, low, high = map(int, match.groups())
                    if (gen <= 65535 and 2 <= theme <= 8 and 0 <= preview <= 4 and
                            style <= 1 and 10 <= low <= high <= 1200):
                        self.art_request = (gen, theme, preview,
                                            ("classic", "linked")[style], low / 10, high / 10)
            elif line.startswith(b"@ART_ACK "):
                match = re.fullmatch(
                    rb"@ART_ACK (\d+) (ok|clear|decode_error|rejected|dropped)\r?", line)
                if match and int(match[1]) <= 65535:
                    self.art_ack = int(match[1])
                    self.art_ack_status = match[2].decode("ascii")
            elif line.startswith(b"@ART_STAT "):
                self.art_stats = dict(re.findall(
                    r"(\w+)=([.\d]+)", line.decode("ascii", errors="replace")))
                print(line.decode("ascii", errors="replace").strip(), flush=True)
            elif line.startswith(b"@SD_MEDIA "):
                self.sd_media = line.strip() == b"@SD_MEDIA 1"
            elif line.strip() in (b"@USAGE_HELLO 1", b"@USAGE_HELLO 2"):
                self.account_protocol = True
                self.expiry_protocol = line.strip() == b"@USAGE_HELLO 2"
            elif line.strip() in (b"@API_SOURCE auto", b"@API_SOURCE official", b"@API_SOURCE morecode"):
                self.api_mode_request = line.split()[1].decode("ascii")
            elif line.strip() in (b"@ROTATE_MODE off", b"@ROTATE_MODE active", b"@ROTATE_MODE all"):
                self.rotation_mode_request = line.split()[1].decode("ascii")
            elif line.strip() == b"@MODEL_TEST_HELLO 1":
                self.model_test_supported = True
            elif line.strip() == b"@MODEL_PICK_HELLO 1":
                self.model_picker_supported = True
            elif line.strip() == b"@ROTATE_HELLO 1":
                self.rotation_supported = True
            elif line.startswith(b"@MODEL_PICK "):
                match = re.fullmatch(rb"@MODEL_PICK (pin|cancel|open|close|confirm) (\d{1,10}) (\d{1,5}) ([0-6])\r?", line)
                if match:
                    action = match[1].decode("ascii")
                    context, request, key = map(int, match.groups()[1:])
                    if context <= 0xFFFFFFFF and request <= 65535:
                        self.model_picker_requests.append((action, context, request, key))
                        self.model_picker_requests = self.model_picker_requests[-16:]
                        if action != "open":
                            print(f"[MODEL_CONTROL] {action} key={key}", flush=True)
            elif line.startswith(b"@MODEL_TEST_ERROR "):
                self.model_test_status["error"] = line.split()[1].decode("ascii", "replace")[:40]
                print(line.decode("ascii", "replace").strip(), flush=True)
            elif line.startswith(b"@MODEL_TEST "):
                match = re.fullmatch(rb"@MODEL_TEST active=([01]) step=([0-4]) model=([0-4])\r?", line)
                if match:
                    active, step, model = map(int, match.groups())
                    old = self.model_test_status
                    self.model_test_status = dict(active=bool(active), step=step, model=model,
                                                  error=old["error"])
                    if (old["active"], old["step"]) != (bool(active), step):
                        print(line.decode("ascii", "replace").strip(), flush=True)
        self.pending = self.pending[-4096:]
        return nav_delta

    def request_model_test(self, start):
        if self.port is None or not self.model_test_supported:
            self.model_test_status["error"] = "disconnected" if self.port is None else "unsupported"
            return False
        self.sequence = (self.sequence + 1) & 0xFFFF
        data = model_test_packet(self.sequence, start)
        if self.port.write(data) != len(data):
            raise RuntimeError("Incomplete model-test control write")
        self.awaiting[self.sequence] = time.monotonic()
        self.model_test_status["error"] = ""
        return True

    def send_picker(self, payload, now):
        if not self.model_picker_supported:
            return
        if self.transport == "ble" and self.port.pending_bytes >= 3072:
            return
        if payload == self.last_picker_payload and now - self.last_picker_send < 1:
            return
        self.sequence = (self.sequence + 1) & 0xFFFF
        data = model_picker_packet(self.sequence, **payload)
        if self.port.write(data) != len(data):
            raise RuntimeError("Incomplete model selection write")
        self.awaiting[self.sequence] = now
        self.last_picker_payload, self.last_picker_send = payload, now

    def send_rotation_mode(self, mode, now):
        if not self.rotation_supported or mode == self.last_rotation_mode:
            return
        self.sequence = (self.sequence + 1) & 0xFFFF
        data = rotation_packet(self.sequence, mode)
        if self.port.write(data) != len(data):
            raise RuntimeError("Incomplete rotation-mode write")
        self.awaiting[self.sequence] = now
        self.last_rotation_mode = mode

    def send(self, view_args):
        # State snapshots supersede old snapshots. Leave room for immediate
        # model/button commands and retry with the latest view on the next tick.
        if self.transport == "ble" and self.port.pending_bytes >= 3072:
            return
        if not self.account_protocol:
            view_args = {k: v for k, v in view_args.items() if k not in ACCOUNT_KEYS}
        elif not self.expiry_protocol:
            view_args = {k: v for k, v in view_args.items() if k != "membership_expires_at"}
        # Resend full labels periodically to recover from a rejected/lost frame.
        full = self.last_payload != view_args or self.sent % 5 == 0
        self.sequence = (self.sequence + 1) & 0xFFFF
        data = packet(self.sequence, **view_args) if full else packet(self.sequence)
        if self.port.write(data) != len(data):
            raise RuntimeError("Incomplete device write")
        self.awaiting[self.sequence] = time.monotonic()
        while len(self.awaiting) > 16:
            del self.awaiting[next(iter(self.awaiting))]
        self.last_payload = view_args
        self.sent += 1

    def goodbye(self):
        if self.port is not None:
            self.sequence = (self.sequence + 1) & 0xFFFF
            self.port.write(disconnect_packet(self.sequence))
            if self.transport == "ble":
                self.port.flush(timeout=0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--transport", choices=("auto", "usb", "ble"), default="auto")
    parser.add_argument("--bluetooth-address", help="Previously paired board address; USB registration is preferred")
    parser.add_argument("--codex-dir", type=Path, default=Path(os.environ.get("CODEX_HOME",str(Path.home()/".codex"))))
    parser.add_argument("--aliases", type=Path, default=PROJECT/"host/aliases.json")
    parser.add_argument("--seconds", type=float, default=0, help="0 keeps running until stopped")
    parser.add_argument("--rotate", type=float, default=5)
    parser.add_argument("--settings", type=Path, default=PROJECT/"host/settings.json")
    parser.add_argument("--theme", choices=("light", "dark", "beach", "pixel", "mechanical", "paper", "glass", "vangogh"),help="Override settings.json for this process")
    parser.add_argument("--transition-ms",type=int,help="Expression transition duration, 200..2000ms")
    parser.add_argument("--demo", action="store_true", help="Explicitly labeled synthetic state cycle")
    parser.add_argument("--once", action="store_true", help="Read status once without opening USB")
    parser.add_argument("--dry-run", action="store_true", help="Print status, do not open USB")
    args = parser.parse_args()
    if args.seconds < 0 or args.rotate <= 0:
        parser.error("Invalid duration or rotation interval")
    if args.transition_ms is not None and not 200<=args.transition_ms<=2000:
        parser.error("--transition-ms must be from 200 to 2000")
    settings=Settings(args.settings,theme=args.theme,transition_ms=args.transition_ms)
    aliases = json.loads(args.aliases.read_text()) if args.aliases.exists() else {}
    detector = Detector(args.codex_dir.resolve(), aliases)
    if args.once:
        snapshot = detector.snapshot()
        print(json.dumps({"source":"local-cli-read-only", "error":detector.error,
                          "sessions":[s.public() for s in snapshot]},ensure_ascii=False,indent=2))
        return 1 if detector.error else 0
    initial_settings = settings.poll()
    chooser = Chooser(args.rotate, mode=initial_settings["rotation_mode"])
    usage_reader = UsageRouter(args.codex_dir.resolve())
    connection = Connection(args.port, transport=args.transport, bluetooth_address=args.bluetooth_address)
    model_control = ModelControl(args.codex_dir.resolve())
    # Local lock is separate from Codex's own locks and databases.
    lock_path = PROJECT/"logs/bridge.lock"
    lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another bridge is already running",file=sys.stderr)
            return 2
        stopped = False
        def stop(_signum,_frame):
            nonlocal stopped
            stopped = True
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        started = time.monotonic()
        last_snapshot = None
        last_send = -10.0
        last_progress = started
        last_config=None
        last_config_error=None
        desktop = DesktopHub() if not args.dry_run else None
        last_desktop_send = -10.0
        last_context = None
        desktop_sequence = 0
        pending_navigation = 0
        print("[START] " + ("演示模式（非实时）" if args.demo else "Codex CLI 状态 + MoreCode 账户统计"),flush=True)
        try:
            while not stopped and (args.seconds == 0 or time.monotonic()-started < args.seconds):
                now = time.monotonic()
                appearance=settings.poll()
                chooser.configure(appearance["rotation_mode"], now, args.rotate)
                if appearance!=last_config:
                    print("[CONFIG] "+json.dumps(appearance,ensure_ascii=False),flush=True)
                    last_config=appearance
                if settings.error!=last_config_error:
                    if settings.error:print("[CONFIG] 保留上次有效设置："+settings.error,flush=True)
                    last_config_error=settings.error
                if args.demo:
                    state = STATES[int((now-started)/4) % len(STATES)]
                    sessions = [Session("demo","表情演示","表情演示",state)]
                else:
                    sessions = detector.snapshot(now)
                sessions = sessions[:255]
                model_control.poll(sessions, now)
                model_control.sync_models(sessions)
                pinned = model_control.pinned(now)
                if pinned:
                    chooser.current, chooser.manual_until = pinned, now + 1
                selected = chooser.choose(sessions,now)
                ordered_sessions = chooser.ordered(sessions)
                signature = [(s.id,s.short,s.state,s.children) for s in sessions]
                if signature != last_snapshot:
                    print("[STATUS] "+json.dumps([{ "name":s.short,"state":s.state,"children":s.children} for s in sessions],ensure_ascii=False),flush=True)
                    if detector.error:
                        print("[SOURCE] "+detector.error,flush=True)
                    last_snapshot = signature
                if not args.dry_run:
                    nav_delta = desktop.poll(now) + pending_navigation
                    pending_navigation = 0
                    try:
                        connection.poll_transport(now)
                        if connection.port is None:
                            if connection._opening is None and now >= connection.next_retry:
                                connection.begin_open()
                            connection.poll_open()
                        if connection.port is not None:
                            nav_delta += connection.receive()
                    except (serial.SerialException,OSError,RuntimeError) as exc:
                        print(f"[LINK] {exc}; retry in 2 seconds",flush=True)
                        connection.retry_after_failure()
                    for action in connection.model_picker_requests:
                        model_control.handle(*action, now)
                    connection.model_picker_requests.clear()
                    pinned = model_control.pinned(now)
                    if pinned:
                        selected = next((s for s in sessions if s.id == pinned), selected)
                        chooser.current, chooser.manual_until = pinned, now + 1
                    if nav_delta and not pinned:
                        selected = chooser.step(sessions, nav_delta, now)
                        ordered_sessions = chooser.ordered(sessions)
                        if selected:
                            direction = "下一对话" if nav_delta > 0 else "上一对话"
                            print(f"[NAV] {direction}：{selected.short}", flush=True)
                    mode_request = desktop.api_mode_request or connection.api_mode_request
                    if mode_request:
                        try:
                            appearance = settings.set_api_mode(mode_request)
                        except (OSError, ValueError) as exc:
                            print(f"[API] 无法保存来源设置：{type(exc).__name__}", flush=True)
                        desktop.api_mode_request = connection.api_mode_request = None
                    rotation_request = (desktop.rotation_mode_request or
                                        connection.rotation_mode_request)
                    if rotation_request:
                        try:
                            appearance = settings.set_rotation_mode(rotation_request)
                            chooser.configure(appearance["rotation_mode"], now, args.rotate)
                        except (OSError, ValueError) as exc:
                            print(f"[ROTATION] 无法保存轮播设置：{type(exc).__name__}", flush=True)
                        desktop.rotation_mode_request = connection.rotation_mode_request = None
                    expiry_request = desktop.membership_expiry_request
                    if expiry_request is not None:
                        try:
                            appearance = settings.set_membership_expires_at(expiry_request)
                        except (OSError, ValueError) as exc:
                            print(f"[API] 无法保存会员到期时间：{type(exc).__name__}", flush=True)
                        desktop.membership_expiry_request = None
                    mode = appearance["api_mode"]
                    usage = usage_reader.snapshot(mode, selected, sessions, now)
                    view = dict(state=selected.state if selected else ("offline" if detector.error else "idle"),
                                        total=len(sessions), index=sessions.index(selected) if selected else 0,
                                        pending=sum(s.state=="waiting" for s in sessions),
                                        duration_seconds=selected.duration_seconds if selected else 0,
                                        labels=labels(selected,ordered_sessions,bool(detector.error),args.demo),
                                        balance_quota=usage.balance_quota,
                                        spent_quota=usage.spent_quota,
                                        request_count=usage.request_count,
                                        today_request_count=usage.today_request_count,
                                        today_quota=usage.today_quota,
                                        today_prompt_tokens=usage.today_prompt_tokens,
                                        today_completion_tokens=usage.today_completion_tokens,
                                        input_tps_x10=usage.input_tps_x10,
                                        output_tps_x10=usage.output_tps_x10,
                                        quota_per_unit=usage.quota_per_unit,
                                        models=usage.models,
                                        usage_stale=usage.stale,
                                        api_source=usage.api_source,
                                        account_kind=usage.account_kind,
                                        week_remaining_x10=usage.week_remaining_x10,
                                        week_reset=usage.week_reset,
                                        plan_name=usage.plan_name,
                                        **appearance)
                    view.pop("rotation_mode", None)
                    context_key = (selected.id if selected else None,
                                   selected.model if selected else None,
                                   view["state"], view["total"], view["index"], view["labels"])
                    urgent = context_key != last_context
                    publish_desktop = urgent or now - last_desktop_send >= 0.5
                    if publish_desktop:
                        desktop_sequence = (desktop_sequence + 1) & 0xFFFF
                        desktop.publish(packet(desktop_sequence, **view), now,
                                        model=selected.model if selected else "")
                        last_desktop_send = now
                    try:
                        if connection.port is not None:
                            connection.send_rotation_mode(appearance["rotation_mode"], now)
                            picker_payload = model_control.context(selected, now)
                            model_name = (selected.model or "").lower() if selected else ""
                            model_key = next(
                                (key for name, key in
                                 (("sol", 1), ("terra", 2), ("luna", 3), ("astra", 4))
                                 if name in model_name),
                                0)
                            if model_key != connection.last_model:
                                connection.sequence = (connection.sequence + 1) & 0xFFFF
                                connection.port.write(
                                    model_packet(connection.sequence,
                                                 model_key))
                                connection.last_model = model_key
                                print(f"[MODEL] {selected.model if selected else ''} -> key={model_key}",
                                      flush=True)
                            # Artwork is always SD-local, including when the card
                            # is unavailable. USB carries metadata, never video.
                            if ((urgent or now-last_send >= 0.5) and
                                    (view != connection.last_payload or now-last_send >= 1)):
                                connection.send(view)
                                last_send = now
                            connection.send_picker(picker_payload, now)
                            if now-connection.last_ack > 8:
                                raise RuntimeError("Device ACK timeout")
                        if desktop.model_test_request is not None:
                            requested = desktop.model_test_request
                            desktop.model_test_request = None
                            connection.request_model_test(requested)
                    except (serial.SerialException,OSError,RuntimeError) as exc:
                        print(f"[LINK] {exc}; retry in 2 seconds",flush=True)
                        connection.retry_after_failure()
                    last_context = context_key
                    if publish_desktop:
                        desktop.publish_rotation_mode(appearance["rotation_mode"])
                        desktop.publish_test_status(dict(connection.model_test_status,
                            available=connection.port is not None and connection.model_test_supported
                                      and now-connection.last_ack < 4))
                if args.seconds and now-last_progress >= 5:
                    elapsed=now-started
                    fraction=min(1,elapsed/args.seconds)
                    bars=int(fraction*10)
                    print(f"检测 [{'█'*bars}{'░'*(10-bars)}] {fraction:.0%} | {elapsed:.1f}/{args.seconds:g}s | ETA {max(0,args.seconds-elapsed):.1f}s",flush=True)
                    last_progress=now
                deadline = now + 0.20
                while not stopped and time.monotonic() < deadline:
                    if not args.dry_run and connection.port is not None:
                        try:
                            pending_navigation += connection.receive()
                        except (serial.SerialException, OSError, RuntimeError) as exc:
                            print(f"[LINK] {exc}; retry in 2 seconds", flush=True)
                            connection.retry_after_failure()
                    time.sleep(0.02)
        finally:
            try:
                connection.goodbye()
            except (serial.SerialException, OSError, RuntimeError):
                pass
            model_control.close()
            usage_reader.close()
            connection.close(wait=True)
            if desktop is not None:
                desktop.close()
        print("[STOP] 已通知设备断开，通信连接已释放。",flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
