"""Bounded artwork chunks multiplexed with the existing CRC-protected telemetry."""
import binascii
import struct

from protocol import MAGIC

WIDTH, HEIGHT = 560, 416
MAX_JPEG = 96 * 1024
CHUNK = 2048
KEYS = (None, "sol", "terra", "luna", "astra")


def art_packets(frame, key, jpeg, generation=0):
    if key not in range(5) or len(jpeg) > MAX_JPEG:
        raise ValueError("Invalid artwork")
    if (key == 0) != (len(jpeg) == 0):
        raise ValueError("Empty artwork must use key zero")
    for offset in range(0, max(1, len(jpeg)), CHUNK):
        body = struct.pack("<BBHHII", 3, key, frame & 0xFFFF, generation & 0xFFFF,
                           offset, len(jpeg)) + jpeg[offset:offset + CHUNK]
        framed = struct.pack("<HH", len(body), frame & 0xFFFF) + body
        yield MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))
