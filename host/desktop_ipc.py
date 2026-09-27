"""Small, bounded Unix datagram bridge for the desktop LVGL client."""

import errno
import fcntl
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import struct
import time
from settings import validate_expiry

SOCKET_NAME = "bridge.sock"
LOCK_NAME = "ipc.lock"
MAX_CLIENTS = 8
MAX_READS = 64
MAX_RECEIVES = 32
EXPIRY_SECONDS = 5.0
VIEW_MAGIC = b"CDXM\x01"
TEST_STATUS = b"MODEL_TEST_STATUS "
ROTATION_STATUS = b"ROTATION_STATUS "


def encode_view(packet, model):
    """Desktop-only envelope; the enclosed ESP packet stays byte-for-byte intact."""
    model = model[:160] if isinstance(model, str) else ""
    metadata = json.dumps({"model": model}, ensure_ascii=True).encode("ascii")
    return VIEW_MAGIC + struct.pack("<H", len(metadata)) + metadata + packet


def decode_view(data):
    if not data.startswith(VIEW_MAGIC):
        return data, ""
    if len(data) < 7:
        raise ValueError("truncated desktop envelope")
    size, = struct.unpack_from("<H", data, 5)
    if not 0 < size <= 2048 or len(data) <= 7 + size:
        raise ValueError("invalid desktop metadata length")
    metadata = json.loads(data[7:7 + size])
    if not isinstance(metadata, dict) or not isinstance(metadata.get("model"), str):
        raise ValueError("invalid desktop model")
    return data[7 + size:], metadata["model"][:160]


def runtime_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        root = Path(base)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or os.stat(root).st_uid != os.getuid():
            raise PermissionError("unsafe XDG_RUNTIME_DIR")
        directory = root / "codex-pet"
    else:
        directory = Path(f"/tmp/codex-pet-{os.getuid()}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_directory(directory)
    os.chmod(directory, 0o700)
    return directory


def _validate_directory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise PermissionError("IPC directory must be a real directory")
    info = directory.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError("IPC directory ownership or permissions are unsafe")


def _peer_path(directory, address):
    if not isinstance(address, str):
        return None
    path = Path(address)
    directory = Path(directory).resolve()
    try:
        if path.parent != directory or path.is_symlink():
            return None
    except OSError:
        return None
    return path


class DesktopHub:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else runtime_dir()
        if directory is not None:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            _validate_directory(self.directory)
        self.socket_path = self.directory / SOCKET_NAME
        self.lock_path = self.directory / LOCK_NAME
        self._lock = open(self.lock_path, "a+b")
        os.chmod(self.lock_path, 0o600)
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            self._lock.close()
            raise RuntimeError("desktop IPC hub already running") from exc
        if self.socket_path.exists() or self.socket_path.is_symlink():
            try:
                info = self.socket_path.lstat()
                if (self.socket_path.is_symlink() or not stat.S_ISSOCK(info.st_mode) or
                        info.st_uid != os.getuid()):
                    raise RuntimeError("bridge socket already exists")
                self.socket_path.unlink()
            except OSError as exc:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                raise RuntimeError("cannot recover bridge socket") from exc
            except RuntimeError:
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
                self._lock.close()
                raise
        self._lock.flush()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.sock.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o600)
            self.sock.setblocking(False)
        except Exception:
            self.sock.close()
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
            raise
        self.peers = {}
        self.latest = None
        self.api_mode_request = None
        self.membership_expiry_request = None
        self.rotation_mode_request = None
        self.model_test_request = None
        self.test_status = None
        self.rotation_status = None
        self._closed = False

    def _active(self, now):
        for peer, seen in list(self.peers.items()):
            if now - seen >= EXPIRY_SECONDS:
                self.peers.pop(peer, None)

    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        self._active(now)
        total = 0
        for _ in range(MAX_READS):
            try:
                message, address = self.sock.recvfrom(256)
            except BlockingIOError:
                break
            except OSError:
                break
            peer = _peer_path(self.directory, address)
            if peer is None:
                continue
            if message == b"SUB":
                peer = str(peer)
                if peer not in self.peers and len(self.peers) >= MAX_CLIENTS:
                    continue
                self.peers[peer] = now
                if self.latest is not None:
                    self._send(self.latest, peer)
                if self.test_status is not None:
                    self._send(self.test_status, peer)
                if self.rotation_status is not None:
                    self._send(self.rotation_status, peer)
            elif message == b"UNSUB":
                self.peers.pop(str(peer), None)
            elif message in (b"NAV 1", b"NAV +1"):
                if str(peer) in self.peers:
                    self.peers[str(peer)] = now
                    total += 1
            elif message in (b"NAV -1",):
                if str(peer) in self.peers:
                    self.peers[str(peer)] = now
                    total -= 1
            elif message in (b"SOURCE auto", b"SOURCE official", b"SOURCE morecode"):
                if str(peer) in self.peers:
                    self.api_mode_request = message.split()[1].decode("ascii")
            elif message.startswith(b"EXPIRY ") and str(peer) in self.peers:
                try:
                    self.membership_expiry_request = validate_expiry(message[7:].decode("ascii"))
                except (ValueError, UnicodeError):
                    pass
            elif message in (b"ROTATION off", b"ROTATION active", b"ROTATION all"):
                if str(peer) in self.peers:
                    self.rotation_mode_request = message.split()[1].decode("ascii")
            elif message in (b"MODEL_TEST 0", b"MODEL_TEST 1") and str(peer) in self.peers:
                self.model_test_request = message.endswith(b"1")
        return max(-8, min(8, total))

    def _send(self, packet, peer):
        try:
            self.sock.sendto(packet, peer)
        except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
            if getattr(exc, "errno", None) not in (errno.EAGAIN, errno.EWOULDBLOCK):
                self.peers.pop(peer, None)

    def publish(self, packet: bytes, now=None, *, model=None):
        if not isinstance(packet, bytes):
            raise TypeError("packet must be bytes")
        if model is not None:
            packet = encode_view(packet, model)
        now = time.monotonic() if now is None else now
        self.latest = packet
        self._active(now)
        for peer in list(self.peers):
            self._send(packet, peer)

    def publish_test_status(self, status):
        self.test_status = TEST_STATUS + json.dumps(status, separators=(",", ":")).encode("ascii")
        for peer in list(self.peers):
            self._send(self.test_status, peer)

    def publish_rotation_mode(self, mode):
        if mode not in ("off", "active", "all"):
            raise ValueError("Invalid conversation rotation mode")
        status = ROTATION_STATUS + mode.encode("ascii")
        if status == self.rotation_status:
            return
        self.rotation_status = status
        for peer in list(self.peers):
            self._send(status, peer)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.sock.close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        self._lock.close()


class DesktopClient:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else runtime_dir()
        if directory is not None:
            _validate_directory(self.directory)
        self.bridge = self.directory / SOCKET_NAME
        self.path = self.directory / f"client-{os.getpid()}-{secrets.token_hex(8)}.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.sock.setblocking(False)
        self._last_subscribe = None
        self._closed = False
        self.model = ""
        self.model_test_status = {"available": False, "active": False, "step": 0, "model": 0, "error": ""}
        self.model_test_updated = 0.0
        self.rotation_mode = "active"

    def subscribe(self, now=None):
        now = time.monotonic() if now is None else now
        if self._last_subscribe is not None and now - self._last_subscribe < 1.0:
            return False
        try:
            self.sock.sendto(b"SUB", str(self.bridge))
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
        self._last_subscribe = now
        return True

    def receive(self):
        packets = []
        for _ in range(MAX_RECEIVES):
            try:
                packet, sender = self.sock.recvfrom(65535)
            except BlockingIOError:
                break
            if sender != str(self.bridge):
                continue
            if packet.startswith(TEST_STATUS):
                try:
                    status = json.loads(packet[len(TEST_STATUS):])
                    if (not isinstance(status, dict) or
                            type(status.get("available")) is not bool or
                            type(status.get("active")) is not bool or
                            type(status.get("step")) is not int or not 0 <= status["step"] <= 4 or
                            type(status.get("model")) is not int or not 0 <= status["model"] <= 4 or
                            not isinstance(status.get("error"), str)):
                        continue
                except (ValueError, UnicodeError):
                    continue
                self.model_test_status = status
                self.model_test_updated = time.monotonic()
                continue
            if packet in (b"ROTATION_STATUS off", b"ROTATION_STATUS active",
                          b"ROTATION_STATUS all"):
                self.rotation_mode = packet.split()[1].decode("ascii")
                continue
            if packet.startswith(ROTATION_STATUS):
                continue
            try:
                packet, model = decode_view(packet)
            except (ValueError, UnicodeError):
                continue
            self.model = model
            packets.append(packet)
        return packets

    def navigate(self, delta):
        if delta not in (-1, 1):
            return False
        try:
            self.sock.sendto(b"NAV %d" % delta, str(self.bridge))
        except OSError as exc:
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            return False
        return True

    def request_model_test(self, start):
        if type(start) is not bool:
            raise ValueError("Model test requires a boolean")
        try:
            self.subscribe()
            self.sock.sendto(b"MODEL_TEST 1" if start else b"MODEL_TEST 0", str(self.bridge))
        except OSError:
            return False
        return True

    def set_api_mode(self, mode):
        if mode not in ("auto", "official", "morecode"):
            raise ValueError("Invalid API source")
        try:
            self.sock.sendto(b"SOURCE " + mode.encode("ascii"), str(self.bridge))
        except OSError:
            return False
        return True

    def set_rotation_mode(self, mode):
        if mode not in ("off", "active", "all"):
            raise ValueError("Invalid conversation rotation mode")
        try:
            self.subscribe()
            self.sock.sendto(b"ROTATION " + mode.encode("ascii"), str(self.bridge))
        except OSError:
            return False
        return True

    def set_membership_expiry(self, value):
        value = validate_expiry(value)
        try:
            self.sock.sendto(b"EXPIRY " + value.encode("ascii"), str(self.bridge))
        except OSError:
            return False
        return True

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.sock.sendto(b"UNSUB", str(self.bridge))
        except OSError:
            pass
        self.sock.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
