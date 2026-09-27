import binascii
import io
import json
import struct

import pytest

from media_upload import (ABORT, BEGIN, CHUNK, END, FILES, ASTRA_FILES, MAGIC, MEDIA, MAX_CHUNK_BYTES,
                          MediaUploader, frame, media_packets, media_payload,
                          open_serial, parse_ack, required_files)


def body(packet):
    size = struct.unpack_from("<H", packet, 4)[0]
    return packet[8:8 + size]


def test_exact_payload_contract_and_chunk_limit():
    packets = list(media_packets("C00.CJP", b"x" * 4097))
    assert [body(packet)[1] for packet in packets] == [BEGIN, CHUNK, CHUNK, CHUNK, END]
    assert [struct.unpack_from("<I", body(packet), 4)[0] for packet in packets[1:-1]] == [0, 2048, 4096]
    assert struct.unpack("<BBBBII", body(packets[0])) == (MEDIA, BEGIN, 0, 0, 4097, binascii.crc32(b"x" * 4097) & 0xffffffff)
    assert len(body(packets[1])) == 14 + 2048
    assert packets[0].startswith(MAGIC)
    with pytest.raises(ValueError):
        media_payload(CHUNK, "C00.CJP", data=b"x" * (MAX_CHUNK_BYTES+1))


def test_required_files_are_fixed_and_manifest_is_valid(tmp_path):
    for name in FILES:
        (tmp_path / name).write_text(json.dumps({"version": 1}) if name == "manifest.json" else name)
    assert [path.name for path in required_files(tmp_path)] == list(FILES)
    (tmp_path / "C08.CJP").unlink()
    with pytest.raises(ValueError, match="C08.CJP"):
        required_files(tmp_path)


class FakePort:
    def __init__(self, *, drop_first=False, bad_prefix=False):
        self.replies = bytearray(b"@MEDIA_HELLO 1\n")
        self.writes = []
        self.dtr = self.rts = None
        self.drop_first = drop_first
        self.bad_prefix = bad_prefix
        self.dropped = False
        self.crc = 0

    @property
    def in_waiting(self):
        return len(self.replies)

    def write(self, data):
        self.writes.append(data)
        payload = body(data)
        sequence = struct.unpack_from("<H", data, 6)[0]
        operation, file_id = payload[1], payload[2]
        if self.drop_first and not self.dropped:
            self.dropped = True
            return len(data)
        if operation == BEGIN:
            self.crc = 0
            offset, checksum = (1, 0) if self.bad_prefix else (0, 0)
        elif operation == CHUNK:
            offset = struct.unpack_from("<I", payload, 4)[0]
            length = struct.unpack_from("<H", payload, 8)[0]
            checksum = binascii.crc32(payload[14:14 + length], self.crc) & 0xffffffff
            self.crc = checksum
            offset += length
        elif operation == END:
            offset = struct.unpack_from("<I", payload, 4)[0]
            checksum = struct.unpack_from("<I", payload, 8)[0]
        else:
            offset, checksum = 0, 0
        complete = 1 if operation == END else 0
        self.replies.extend(f"@MEDIA_ACK {sequence} ok offset={offset} crc={checksum} complete={complete}\n".encode())
        return len(data)

    def read(self, count):
        data = bytes(self.replies[:count])
        del self.replies[:count]
        return data


def make_files(directory):
    for name in FILES:
        (directory / name).write_bytes(b"{}" if name == "manifest.json" else b"clip")


def test_uploader_handshake_streams_and_retries(tmp_path):
    make_files(tmp_path)
    port = FakePort(drop_first=True)
    output = io.StringIO()
    MediaUploader(port, timeout=1, output=output).upload(tmp_path)
    assert any(body(packet)[1] == CHUNK for packet in port.writes)
    assert "100.0%" in output.getvalue()


def test_uploader_negotiates_larger_blocks_without_changing_old_firmware_default(tmp_path):
    make_files(tmp_path)
    (tmp_path / "C00.CJP").write_bytes(b"x"*32769)
    port = FakePort()
    port.replies = bytearray(b"@MEDIA_CHUNK 16384\n@MEDIA_HELLO 1\n")
    uploader = MediaUploader(port, timeout=1, output=io.StringIO())
    uploader.upload(tmp_path)
    chunks = [body(packet) for packet in port.writes if body(packet)[1:3] == bytes((CHUNK, 0))]
    assert [struct.unpack_from("<H", chunk, 8)[0] for chunk in chunks] == [16384, 16384, 1]
    assert MediaUploader(FakePort()).chunk_bytes == 2048


def test_chunk_retries_use_short_deadline_but_queries_keep_long_deadline():
    from media_upload import QUERY
    uploader = MediaUploader(FakePort(), output=io.StringIO())
    deadlines = []
    def ack(sequence, timeout):
        deadlines.append(timeout)
        return {"sequence": sequence}
    uploader._ack = ack
    uploader.send(frame(0, media_payload(CHUNK, "A00.CJP", data=b"x")))
    uploader.send(frame(1, media_payload(QUERY, "A00.CJP")))
    assert deadlines == [5, 60]


def test_layer_upload_only_appends_three_whitelisted_files(tmp_path):
    from media_format import HEADER, ENTRY, header
    for name, clip_id in zip(ASTRA_FILES, (7, 6, 7)):
        jpeg = b"layer-test"
        table = b"".join(ENTRY.pack(HEADER.size + 2*ENTRY.size + i*len(jpeg),
                                  len(jpeg), binascii.crc32(jpeg)) for i in range(2))
        (tmp_path / name).write_bytes(header(clip_id, 30, 2, binascii.crc32(table)) + table + jpeg*2)
    original = tmp_path / "C07.CJP"
    original.write_bytes(b"original unchanged")
    port = FakePort()
    output = io.StringIO()
    MediaUploader(port, timeout=1, output=output).upload(tmp_path, astra_layers=True)
    assert {body(packet)[2] for packet in port.writes} == {10, 11, 12}
    assert original.read_bytes() == b"original unchanged"
    assert "100.0%" in output.getvalue()
    for name in ("../A00.CJP", "A01.CJP", "A00.CJP.NEW"):
        with pytest.raises(ValueError):
            media_payload(BEGIN, name)


def test_resume_prefix_mismatch_aborts_and_restarts(tmp_path):
    make_files(tmp_path)
    port = FakePort(bad_prefix=True)
    with pytest.raises(RuntimeError, match="mismatched resume prefix"):
        MediaUploader(port, timeout=1).upload(tmp_path)
    assert any(body(packet)[1] == ABORT for packet in port.writes)


def test_ack_parser_rejects_wrong_shape_and_accepts_exact_contract():
    assert parse_ack("@MEDIA_ACK 7 ok offset=12 crc=34 complete=1") == {
        "sequence": 7, "ok": True, "offset": 12, "crc": 34, "complete": True}
    assert parse_ack("@MEDIA_ACK 7 error reason=bad crc") == {
        "sequence": 7, "ok": False, "reason": "bad crc"}
    assert parse_ack("@MEDIA_ACK 7 ok") is None
    with pytest.raises(ValueError):
        media_payload(BEGIN, "C09.CJP")


def test_ack_recovers_complete_response_after_interleaved_telemetry():
    port = FakePort()
    port.replies = bytearray(
        b"@ASTRA_TIMING output=12 uptime_ms=@MEDIA_ACK 7 ok offset=12 crc=34 complete=1\r\n"
        b"123456\r\n")
    ack = MediaUploader(port, timeout=1)._ack(7)
    assert ack == dict(sequence=7, ok=True, offset=12, crc=34, complete=True)


def test_ack_does_not_accept_interleaving_inside_response_or_wrong_sequence():
    port = FakePort()
    port.replies = bytearray(
        b"@MEDIA_ACK 7 ok offset=12 crc=@MODEL_TEST 0\r\n"
        b"34 complete=1\r\n"
        b"@MEDIA_ACK 6 ok offset=99 crc=99 complete=1\r\n"
        b"@MEDIA_ACK 7 ok offset=12 crc=34 complete=1\r\n")
    assert MediaUploader(port, timeout=1)._ack(7)["offset"] == 12


def test_open_serial_asserts_both_control_lines(monkeypatch):
    created = {}

    class FakeSerial:
        def __init__(self, **kwargs):
            created["kwargs"] = kwargs
            self.dtr = self.rts = False
            self.port = None

        def open(self):
            created["opened"] = True

    import media_upload
    monkeypatch.setattr(media_upload.serial, "Serial", FakeSerial)
    port = open_serial("/dev/test")
    assert port.dtr is True and port.rts is True
    assert port.port == "/dev/test" and created["opened"] is True
