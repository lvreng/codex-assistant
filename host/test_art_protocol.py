import binascii
from pathlib import Path
import queue
import struct
import subprocess
from types import SimpleNamespace

import pytest

from art_protocol import MAX_JPEG, art_packets
from art_stream import ArtStream
from bridge import Connection
from protocol import packet
from test_bridge import FakePort


HARNESS = r"""
#include "art_receiver.h"
#include "pet_protocol.h"
#include <iostream>
#include <string>
#include <cstdio>
int main() {
    Pet::Receiver receiver;
    Artwork::Assembly assembly;
    uint8_t buffer[Artwork::Capacity] = {};
    std::string hex;
    while (std::cin >> hex) {
        int result = -1;
        for (size_t i = 0; i < hex.size(); i += 2) {
            unsigned value = 0;
            if (std::sscanf(hex.c_str()+i, "%2x", &value) != 1) return 1;
            if (receiver.feed(value) && receiver.is_art())
                result = assembly.feed(receiver.payload(), receiver.payload_size(), 7, buffer);
        }
        std::cout << result << ' ' << assembly.size << ' ' << receiver.bad << '\n';
    }
}
"""


@pytest.fixture(scope="module")
def assembly(tmp_path_factory):
    folder = tmp_path_factory.mktemp("art_receiver")
    source, binary = folder / "main.cpp", folder / "main"
    source.write_text(HARNESS)
    subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-fsanitize=address,undefined", "-I",
                    str(Path(__file__).resolve().parents[1] / "main"),
                    str(source), "-o", str(binary)], check=True)
    return binary


def run(binary, chunks):
    result = subprocess.run([str(binary)], input="\n".join(c.hex() for c in chunks),
                            text=True, capture_output=True, check=True)
    return [tuple(map(int, line.split())) for line in result.stdout.splitlines()]


def edit_body(chunk, at, value, fmt="<I"):
    chunk = bytearray(chunk)
    struct.pack_into(fmt, chunk, 8 + at, value)
    struct.pack_into("<H", chunk, len(chunk) - 2,
                     binascii.crc_hqx(chunk[4:-2], 0xFFFF))
    return bytes(chunk)


@pytest.mark.parametrize("length", [1, 2048, 2049, MAX_JPEG])
def test_native_assembly_interleaves_telemetry(assembly, length):
    chunks = list(art_packets(81, 4, bytes(length), 7))
    interleaved = []
    for chunk in chunks:
        interleaved.extend([chunk, packet(81)])
    result = run(assembly, interleaved)
    assert result[-2] == (2, length, 0)
    assert all(row[2] == 0 for row in result)


def test_native_assembly_rejects_bad_bounds_generation_order_and_recovers(assembly):
    chunks = list(art_packets(1, 4, b"x" * 4097, 7))
    inputs = [
        edit_body(chunks[0], 10, MAX_JPEG + 1),
        edit_body(chunks[0], 6, 0xFFFFFFFF),
        edit_body(chunks[0], 4, 6, "<H"),
        chunks[1], chunks[0], chunks[2], *chunks,
        *art_packets(2, 0, b"", 7),
    ]
    results = run(assembly, inputs)
    assert [r[0] for r in results] == [0, 0, 4, 0, 1, 0, 1, 1, 2, 3]
    assert results[-1] == (3, 0, 0)


def test_corrupted_frame_is_not_assembled(assembly):
    chunk = list(art_packets(1, 2, b"image", 7))[0]
    bad = chunk[:-1] + bytes([chunk[-1] ^ 1])
    assert run(assembly, [bad, chunk]) == [(-1, 0, 1), (2, 5, 1)]


@pytest.mark.parametrize("key,data", [(5, b"x"), (0, b"x"), (1, b""), (1, bytes(MAX_JPEG + 1))])
def test_sender_rejects_invalid_frames(key, data):
    with pytest.raises(ValueError):
        list(art_packets(1, key, data))


def test_negotiation_requires_valid_device_request_and_keeps_navigation():
    connection = Connection("unused")
    connection.port = FakePort(
        b"@ART_REQUEST 7 8 4 1 100 150\r\n@ART_ACK 99 ok\n@NAV 1\n")
    assert connection.receive() == 1
    assert connection.art_request == (7, 8, 4, "linked", 10, 15)
    assert connection.art_ack == 99
    assert connection.art_ack_status == "ok"
    connection.port.data = (
        b"@ART_REQUEST 8 8 9 1 100 150\n@ART_REQUEST 8 8 4 1 150 100\n"
        b"@ART_REQUEST 8 8 4 2 100 150\n")
    connection.receive()
    assert connection.art_request[0] == 7


def test_artwork_ack_status_is_validated_and_cleared_on_disconnect():
    connection = Connection("unused")
    connection.port = FakePort(b"@ART_ACK 8 decode_error\n@ART_ACK 65536 ok\n")
    connection.receive()
    assert connection.art_ack == 8
    assert connection.art_ack_status == "decode_error"
    connection.port.data = b"@ART_ACK 9 garbage\n@ART_ACK 10 ok trailing\n"
    connection.receive()
    assert connection.art_ack == 8
    connection.port = None
    connection.close()
    assert connection.art_ack_status is None
    assert connection.art_ack is None


def test_stream_waits_for_ack_and_never_backlogs_frames():
    writes = []
    stream = ArtStream()
    stream.worker = object()
    stream.frames = queue.Queue()
    connection = SimpleNamespace(
        art_ack=None, art_request=(7, 8, 4, "linked", 10, 15),
        port=SimpleNamespace(write=lambda data: writes.append(data) or len(data)))
    stream.frames.put((6, 4, b"stale"))
    stream.frames.put((7, 4, bytes(2049)))
    stream.pump(connection, 10)
    stream.pump(connection, 10.001)
    assert len(writes) == 2
    stream.frames.put((7, 4, b"next"))
    stream.pump(connection, 10.1)
    assert len(writes) == 2
    connection.art_ack = 1
    stream.pump(connection, 10.2)
    assert len(writes) == 3
    assert connection.art_ack is None
    assert stream.pending_frame == 2
