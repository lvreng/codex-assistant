"""Upload SD media using the type-4 media protocol."""
from __future__ import annotations

import argparse
import binascii
from collections import deque
from contextlib import nullcontext
import json
import re
import struct
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:
    serial = None

MAGIC = b"\xC0\xDEPT"
MEDIA = 4
CHUNK_BYTES = 2048
MAX_CHUNK_BYTES = 16384
BEGIN, CHUNK, END, ABORT, QUERY = 1, 2, 3, 4, 5
FILES = tuple(f"C{i:02d}.CJP" for i in range(9)) + ("manifest.json",)
ASTRA_FILES = ("A00.CJP", "A06.CJP", "A07.CJP")
FILE_IDS = {name: index for index, name in enumerate(FILES + ASTRA_FILES)}


def frame(sequence, payload):
    framed = struct.pack("<HH", len(payload), sequence & 0xFFFF) + payload
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def _file_id(name):
    if not isinstance(name, str) or name not in FILE_IDS:
        raise ValueError(f"invalid media filename: {name!r}")
    return FILE_IDS[name]


def media_payload(operation, name, *, size=0, checksum=0, offset=0, data=b""):
    file_id = _file_id(name)
    if operation in (BEGIN, END):
        return struct.pack("<BBBBII", MEDIA, operation, file_id, 0, size, checksum)
    if operation == CHUNK:
        if offset < 0 or len(data) > MAX_CHUNK_BYTES:
            raise ValueError("invalid CHUNK")
        return struct.pack("<BBBBIHI", MEDIA, operation, file_id, 0,
                           offset, len(data), binascii.crc32(data) & 0xFFFFFFFF) + data
    if operation in (ABORT, QUERY):
        return struct.pack("<BBBB", MEDIA, operation, file_id, 0)
    raise ValueError("unknown media operation")


def media_packets(name, data, sequence=0):
    checksum = binascii.crc32(data) & 0xFFFFFFFF
    yield frame(sequence, media_payload(BEGIN, name, size=len(data), checksum=checksum))
    for offset in range(0, len(data), CHUNK_BYTES):
        sequence += 1
        yield frame(sequence, media_payload(CHUNK, name, offset=offset,
                                            data=data[offset:offset + CHUNK_BYTES]))
    sequence += 1
    yield frame(sequence, media_payload(END, name, size=len(data), checksum=checksum))


def required_files(directory):
    directory = Path(directory)
    paths = [directory / name for name in FILES]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise ValueError("missing media files: " + ", ".join(missing))
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest.json: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    return paths


def parse_ack(line):
    text = line.decode("ascii", "replace").strip() if isinstance(line, bytes) else line.strip()
    match = re.fullmatch(r"@MEDIA_ACK (\d+) ok offset=(\d+) crc=(\d+) complete=([01])", text)
    if match:
        return {"sequence": int(match.group(1)), "ok": True,
                "offset": int(match.group(2)), "crc": int(match.group(3)),
                "complete": match.group(4) == "1"}
    match = re.fullmatch(r"@MEDIA_ACK (\d+) error reason=(.*)", text)
    if match:
        return {"sequence": int(match.group(1)), "ok": False, "reason": match.group(2)}
    return None


class MediaUploader:
    def __init__(self, port, *, timeout=60, output=None, clock=time.monotonic,
                 sleeper=time.sleep, diagnostic=None):
        self.port = port
        self.timeout = timeout
        self.output = output or sys.stderr
        self.clock = clock
        self.sleeper = sleeper
        self.sequence = 0
        self.pending = b""
        self.started = clock()
        self.last_progress = -1.0
        self.samples = deque(maxlen=30)
        self.chunk_bytes = CHUNK_BYTES
        self.diagnostic = diagnostic

    def _read_line(self, deadline):
        while self.clock() < deadline:
            if b"\n" in self.pending:
                line, self.pending = self.pending.split(b"\n", 1)
                if self.diagnostic is not None:
                    self.diagnostic.write(f"{self.clock()-self.started:.3f} " + line.decode("ascii", "replace") + "\n")
                return line
            self.pending += self.port.read(getattr(self.port, "in_waiting", 0) or 1)
            if not self.pending:
                self.sleeper(0.0002)
        raise TimeoutError("serial response timeout")

    def handshake(self):
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            line = self._read_line(deadline)
            capability = re.fullmatch(rb"@MEDIA_CHUNK (\d+)\r?", line)
            if capability and CHUNK_BYTES <= int(capability[1]) <= MAX_CHUNK_BYTES:
                self.chunk_bytes = int(capability[1])
            if line.decode("ascii", "replace").strip() == "@MEDIA_HELLO 1":
                print(f"SD transfer block: {self.chunk_bytes} bytes", file=self.output, flush=True)
                return
        raise TimeoutError("media handshake timeout")

    def _ack(self, sequence, timeout=None):
        deadline = self.clock() + (self.timeout if timeout is None else timeout)
        while self.clock() < deadline:
            line = self._read_line(deadline)
            # The two device cores can prepend a partial telemetry line to an ACK.
            # Recover only a complete, strictly validated ACK, never corrupted fields.
            marker = line.rfind(b"@MEDIA_ACK ")
            ack = parse_ack(line[marker:] if marker >= 0 else line)
            if ack is None or ack["sequence"] != sequence:
                continue
            if not ack["ok"]:
                raise RuntimeError(f"device rejected sequence {sequence}: {ack['reason']}")
            return ack
        raise TimeoutError(f"no ACK for media sequence {sequence}")

    def send(self, data):
        sequence = self.sequence
        timeout = min(self.timeout, 5) if data[9] == CHUNK else self.timeout
        for attempt in range(3):
            if self.port.write(data) != len(data):
                raise OSError("incomplete serial write")
            try:
                ack = self._ack(sequence, timeout)
                self.sequence = (sequence + 1) & 0xFFFF
                return ack
            except TimeoutError:
                if attempt == 2:
                    raise
                print(f"Retry sequence {sequence}, attempt {attempt+2}/3; retaining SD offset",
                      file=self.output, flush=True)
        raise AssertionError("unreachable")

    def _progress(self, done, total, final=False):
        now = self.clock()
        if not final and self.last_progress >= 0 and now - self.last_progress < 1:
            return
        self.last_progress = now
        elapsed = now - self.started
        self.samples.append((now, done))
        first_time, first_done = self.samples[0]
        span = now-first_time
        rate = (done-first_done) / span if span >= 3 else 0
        eta = (total - done) / rate if rate else None
        bar_width = 24
        filled = int(bar_width * done / total) if total else bar_width
        eta_text = "estimating" if eta is None else f"{eta:.1f}s"
        print(f"Upload [{('#' * filled) + ('-' * (bar_width - filled))}] "
              f"{(done / total * 100 if total else 100):.1f}% | "
              f"{done}/{total} bytes | elapsed {elapsed:.1f}s | {rate/1024:.0f} KiB/s | ETA {eta_text}",
              file=self.output, flush=True)

    def _prefix_crc(self, path, length):
        checksum = 0
        remaining = length
        with path.open("rb") as source:
            while remaining:
                block = source.read(min(CHUNK_BYTES, remaining))
                if not block:
                    raise ValueError("device resume offset exceeds source file")
                checksum = binascii.crc32(block, checksum) & 0xFFFFFFFF
                remaining -= len(block)
        return checksum

    def upload(self, directory, *, astra_layers=False):
        if astra_layers:
            from media_format import inspect_clip
            paths = [Path(directory) / name for name in ASTRA_FILES]
            timing = None
            for path in paths:
                with path.open("rb") as source:
                    info = inspect_clip(source, verify_frames=True)
                if info["key"] != 4 or not info["working"] or info["transition"]:
                    raise ValueError("Invalid Astra layer")
                identity = (info["fps"], info["frames"])
                if timing is not None and timing != identity:
                    raise ValueError("Astra layer timing mismatch")
                timing = identity
        else:
            paths = required_files(directory)
        total = sum(path.stat().st_size for path in paths)
        self.handshake()
        self.started = self.clock()
        done = 0
        current = paths[0].name
        try:
            for path in paths:
                current = path.name
                size = path.stat().st_size
                checksum = 0
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                        checksum = binascii.crc32(block, checksum) & 0xFFFFFFFF
                expected_checksum = checksum
                query = self.send(frame(self.sequence, media_payload(QUERY, current)))
                if query.get("complete") and query["offset"] == size and query["crc"] == expected_checksum:
                    done += size
                    self._progress(done, total)
                    continue
                if query["complete"]:
                    self.send(frame(self.sequence, media_payload(ABORT, current)))
                begin = self.send(frame(self.sequence, media_payload(
                    BEGIN, current, size=size, checksum=expected_checksum)))
                offset = begin["offset"]
                if offset > size:
                    raise RuntimeError(f"invalid resume offset for {current}: {offset}")
                if self._prefix_crc(path, offset) != begin["crc"]:
                    self.send(frame(self.sequence, media_payload(ABORT, current)))
                    begin = self.send(frame(self.sequence, media_payload(
                        BEGIN, current, size=size, checksum=expected_checksum)))
                    offset = begin["offset"]
                    if self._prefix_crc(path, offset) != begin["crc"]:
                        raise RuntimeError(f"mismatched resume prefix for {current}")
                current_checksum = begin["crc"]
                done += offset
                with path.open("rb") as source:
                    source.seek(offset)
                    while True:
                        block = source.read(self.chunk_bytes)
                        if not block:
                            break
                        ack = self.send(frame(self.sequence, media_payload(
                            CHUNK, current, offset=offset, data=block)))
                        expected_offset = offset + len(block)
                        if ack["offset"] != expected_offset:
                            raise RuntimeError(
                                f"invalid ACK offset for {current}: "
                                f"{ack['offset']} != {expected_offset}")
                        checksum_after = binascii.crc32(block, current_checksum) & 0xFFFFFFFF
                        if ack["crc"] != checksum_after:
                            raise RuntimeError(
                                f"invalid ACK crc for {current}: "
                                f"{ack['crc']} != {checksum_after}")
                        offset = expected_offset
                        current_checksum = checksum_after
                        done += len(block)
                        self._progress(done, total)
                end = self.send(frame(self.sequence, media_payload(
                    END, current, size=size, checksum=expected_checksum)))
                if (not end.get("complete") or end["offset"] != size or
                        end["crc"] != expected_checksum):
                    raise RuntimeError(f"invalid END ACK for {current}")
            self._progress(total, total, final=True)
        except BaseException:
            # Keep .NEW files so a transient USB/host failure can resume.
            # Explicit prefix mismatch handling above is the only path that
            # intentionally removes incompatible partial data.
            raise


def open_serial(path):
    if serial is None:
        raise RuntimeError("pyserial is required for uploads")
    port = serial.Serial(port=None, baudrate=115200, timeout=0.05,
                         write_timeout=1, exclusive=True)
    port.dtr = True
    port.rts = True
    port.port = path
    port.open()
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", default="/dev/ttyACM0")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--astra-layers", action="store_true", help="Append Astra layers only; leave existing clips intact")
    parser.add_argument("--diagnostics", type=Path, help="Record device replies for transfer diagnostics")
    args = parser.parse_args(argv)
    with (args.diagnostics.open("w", buffering=1) if args.diagnostics else nullcontext()) as diagnostic:
        with open_serial(args.port) as port:
            MediaUploader(port, timeout=args.timeout, diagnostic=diagnostic).upload(
                args.directory, astra_layers=args.astra_layers)


if __name__ == "__main__":
    main()
