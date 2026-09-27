import binascii
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from protocol import (LABEL_BYTES, MAGIC, packet, model_picker_packet, disconnect_packet,
                      rotation_packet, astra_period_packet)


HARNESS = r"""
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <string>
#include "pet_protocol.h"

int main() {
    Pet::Receiver receiver;
    std::string hex;
    while (std::cin >> hex) {
        if (hex.size() % 2 != 0) return 2;
        for (size_t i = 0; i < hex.size(); i += 2) {
            unsigned value = 0;
            if (std::sscanf(hex.c_str() + i, "%2x", &value) != 1) return 3;
            receiver.feed(static_cast<uint8_t>(value));
        }
        std::cout << receiver.good << ' ' << receiver.bad;
        if (receiver.is_astra_period()) {
            std::cout << ' ' << unsigned(receiver.model_key()) << ' ' << receiver.astra_minimum() << '\n';
            continue;
        }
        if (receiver.good) {
            Pet::View view;
            receiver.copy_view(view);
            std::cout << ' ' << static_cast<unsigned>(view.state)
                      << ' ' << static_cast<unsigned>(view.theme)
                      << ' ' << static_cast<unsigned>(view.total)
                      << ' ' << static_cast<unsigned>(view.index)
                      << ' ' << static_cast<unsigned>(view.pending)
                      << ' ' << static_cast<unsigned>(view.labels[0])
                      << ' ' << static_cast<unsigned>(view.labels[Pet::LabelBytes - 1])
                      << ' ' << view.transition_ms << ' ' << view.duration_seconds
                      << ' ' << view.balance_quota << ' ' << view.today_quota
                      << ' ' << view.input_tps_x10 << ' ' << view.output_tps_x10
                      << ' ' << static_cast<unsigned>(view.model_count);
            if (receiver.has_account())
                std::cout << ' ' << unsigned(view.api_source) << ' ' << unsigned(view.api_mode)
                          << ' ' << unsigned(view.account_kind) << ' ' << view.week_remaining_x10;
            if (receiver.payload_size() == Pet::ExpiryPayloadBytes)
                for (unsigned i = 0; i < 17; ++i) std::cout << ' ' << unsigned(view.membership_expires_at[i]);
        }
        std::cout << '\n';
    }
}
"""


@pytest.fixture(scope="module")
def firmware_harness(tmp_path_factory):
    compiler = next((shutil.which(name) for name in ("c++", "g++", "clang++") if shutil.which(name)), None)
    if compiler is None:
        pytest.skip("system C++ compiler is unavailable")
    build = tmp_path_factory.mktemp("firmware_protocol")
    source = build / "harness.cpp"
    binary = build / "harness"
    source.write_text(HARNESS)
    subprocess.run(
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(Path(__file__).resolve().parents[1] / "main"), str(source), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def run_harness(binary, packets):
    result = subprocess.run(
        [str(binary)],
        input="\n".join(packet.hex() for packet in packets) + "\n",
        check=True,
        capture_output=True,
        text=True,
    )
    return [tuple(map(int, line.split())) for line in result.stdout.splitlines()]


def make_view(theme, labels):
    return packet(
        0x1234,
        state="working",
        total=7,
        index=3,
        pending=2,
        labels=labels,
        theme=theme,
        transition_ms=987,
        duration_seconds=54321,
        balance_quota=123456,
        today_quota=654321,
        input_tps_x10=1234,
        output_tps_x10=5678,
        models=[{"name": "model"}],
        usage_stale=False,
    )


def test_all_theme_ids_round_trip_with_view_and_usage(firmware_harness):
    labels = bytes((index * 37 + 11) % 256 for index in range(LABEL_BYTES))
    themes = ("dark", "light", "beach", "pixel", "mechanical", "paper", "glass", "vangogh")
    results = run_harness(firmware_harness, [make_view(theme, labels) for theme in themes])

    assert len(results) == len(themes)
    for theme_id, result in enumerate(results):
        assert result == (theme_id + 1, 0, 2, theme_id, 7, 3, 2, labels[0], labels[-1], 987, 54321, 123456, 654321, 1234, 5678, 1)


def test_disconnect_packet_crc_length_and_recovery(firmware_harness):
    data = disconnect_packet(65537)
    assert len(data) == 11 and data[4:9] == bytes((1, 0, 1, 0, 8))
    invalid = bytearray(data)
    invalid[-1] ^= 1
    results = run_harness(firmware_harness, [bytes(invalid), data,
                                           make_view("glass", bytes(LABEL_BYTES))])
    assert results[0] == (0, 1)
    assert results[1][:2] == (1, 1)
    assert results[2][:2] == (2, 1)


@pytest.mark.parametrize("theme_id", (9, 255))
def test_receiver_rejects_unknown_theme_with_valid_crc(firmware_harness, theme_id):
    data = bytearray(make_view("glass", bytes(LABEL_BYTES)))
    theme_offset = len(MAGIC) + 4 + 5 + LABEL_BYTES
    data[theme_offset] = theme_id
    crc = binascii.crc_hqx(bytes(data[len(MAGIC) : -2]), 0xFFFF)
    data[-2:] = struct.pack("<H", crc)

    assert run_harness(firmware_harness, [bytes(data)]) == [(0, 1)]


def test_receiver_rejects_bad_crc_and_recovers(firmware_harness):
    valid = make_view("mechanical", bytes(LABEL_BYTES))
    corrupted = bytearray(valid)
    corrupted[len(MAGIC) + 4 + 5] ^= 0x01

    assert run_harness(firmware_harness, [bytes(corrupted), valid]) == [
        (0, 1),
        (1, 1, 2, 4, 7, 3, 2, 0, 0, 987, 54321, 123456, 654321, 1234, 5678, 1),
    ]


def test_official_extension_round_trip_and_legacy_recovery(firmware_harness):
    extended = packet(1, state="thinking", labels=bytes(LABEL_BYTES), api_source="official",
                      api_mode="official", account_kind=1, week_remaining_x10=980,
                      week_reset="09-29 11:29", plan_name="Pro Lite")
    legacy = make_view("glass", bytes(LABEL_BYTES))
    results = run_harness(firmware_harness, [extended, legacy])
    assert results[0][-4:] == (1, 1, 1, 980)
    assert results[1][0:2] == (2, 0)
    assert len(results[1]) == 16


@pytest.mark.parametrize("offset,value", [(0, 3), (1, 3), (2, 3)])
def test_official_extension_rejects_invalid_source_fields(firmware_harness, offset, value):
    data = bytearray(packet(1, state="done", labels=bytes(LABEL_BYTES), api_source="official"))
    data[-43 + offset] = value
    data[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(data[4:-2]), 0xFFFF))
    assert run_harness(firmware_harness, [bytes(data)]) == [(0, 1)]


def test_expiry_extension_round_trip(firmware_harness):
    stamp = "2026-10-22T23:59"
    data = packet(1, state="done", labels=bytes(LABEL_BYTES), api_source="official",
                  membership_expires_at=stamp, input_tps_x10=1234, output_tps_x10=567)
    result = run_harness(firmware_harness, [data])[0]
    assert result[:2] == (1, 0)
    assert result[-17:] == tuple(stamp.encode() + bytes([0]))
    assert result[13:15] == (1234, 567)


def test_expiry_extension_rejects_malformed_date(firmware_harness):
    data = bytearray(packet(1, state="done", labels=bytes(LABEL_BYTES), api_source="official",
                            membership_expires_at="2026-10-22T23:59"))
    data[-19] = ord("x")
    data[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(data[4:-2]), 0xFFFF))
    assert run_harness(firmware_harness, [bytes(data)]) == [(0, 1)]


def test_model_picker_accepts_bitmap_and_rejects_invalid_status(firmware_harness):
    valid = model_picker_packet(31, 2, 63, 6, 0xFEADBEEF, 55, "模型选择测试")
    bad = bytearray(valid)
    bad[9] = 8
    bad[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(bad[4:-2]), 0xFFFF))
    bad_mask = bytearray(valid)
    bad_mask[10] = 64
    bad_mask[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(bad_mask[4:-2]), 0xFFFF))
    bad_current = bytearray(valid)
    bad_current[11] = 7
    bad_current[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(bad_current[4:-2]), 0xFFFF))
    results = run_harness(firmware_harness, [bytes(bad), bytes(bad_mask),
                                            bytes(bad_current), valid])
    assert results[0] == (0, 1)
    assert results[1:3] == [(0, 2), (0, 3)]
    assert results[3][:2] == (1, 3)


def test_rotation_mode_packet_accepts_three_modes_and_rejects_out_of_range(firmware_harness):
    valid = [rotation_packet(index, mode)
             for index, mode in enumerate(("off", "active", "all"), start=1)]
    bad = bytearray(valid[-1])
    bad[9] = 3
    bad[-2:] = struct.pack("<H", binascii.crc_hqx(bytes(bad[4:-2]), 0xFFFF))
    results = run_harness(firmware_harness, [*valid, bytes(bad)])
    assert [result[:2] for result in results] == [(1, 0), (2, 0), (3, 0), (3, 1)]


def test_astra_period_packet_round_trip(firmware_harness):
    valid = [astra_period_packet(1, 0, 10), astra_period_packet(2, 1, 200),
             astra_period_packet(3, 1, 800)]
    assert run_harness(firmware_harness, valid) == [(1, 0, 0, 10), (2, 0, 1, 200), (3, 0, 1, 800)]


def test_astra_layer_media_ids_are_accepted_but_unknown_files_rejected(firmware_harness):
    from media_upload import frame, media_payload, ASTRA_FILES, QUERY
    packets = [frame(i, media_payload(QUERY, name)) for i, name in enumerate(ASTRA_FILES)]
    packets.append(frame(3, bytes((4, QUERY, 13, 0))))
    results = run_harness(firmware_harness, packets)
    assert [result[:2] for result in results] == [(1, 0), (2, 0), (3, 0), (3, 1)]


def test_large_media_chunks_fit_receiver_and_leave_view_packets_intact(firmware_harness):
    from media_upload import frame, media_payload, CHUNK
    data = bytes(range(256))*64
    transfer = frame(1, media_payload(CHUNK, "A00.CJP", data=data))
    results = run_harness(firmware_harness, [transfer, make_view("glass", bytes(LABEL_BYTES))])
    assert [result[:2] for result in results] == [(1, 0), (2, 0)]


@pytest.mark.parametrize("style,minimum", [(2, 100), (1, 9), (0, 801)])
def test_astra_period_rejects_invalid_ranges(firmware_harness, style, minimum):
    with pytest.raises(ValueError):
        astra_period_packet(0, style, minimum)
    body = struct.pack("<HHBBH", 4, 0, 10, style, minimum)
    invalid = MAGIC + body + struct.pack("<H", binascii.crc_hqx(body, 0xFFFF))
    assert run_harness(firmware_harness, [invalid])[0][:2] == (0, 1)
