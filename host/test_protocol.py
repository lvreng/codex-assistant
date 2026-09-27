import struct

from protocol import LABEL_BYTES, MAGIC, MODEL_COUNT, packet, rotation_packet


def test_morecode_packet_contains_all_telemetry():
    data = packet(
        23,
        state="working",
        total=2,
        index=1,
        pending=0,
        labels=bytes(LABEL_BYTES),
        balance_quota=17_122_315,
        spent_quota=102_887_685,
        request_count=2895,
        today_request_count=1257,
        today_quota=33_970_000,
        today_prompt_tokens=68_000_000,
        today_completion_tokens=919_546,
        input_tps_x10=12_483,
        output_tps_x10=428,
        quota_per_unit=500_000,
        models=[
            {
                "name": "gpt-5.6-sol",
                "quota": 20_000_000,
                "prompt_tokens": 50_000_000,
                "completion_tokens": 700_000,
                "request_count": 900,
            },
            {
                "name": "gpt-5.4",
                "quota": 13_970_000,
                "prompt_tokens": 18_000_000,
                "completion_tokens": 219_546,
                "request_count": 357,
            },
        ],
        usage_stale=False,
    )
    body_length = struct.unpack_from("<H", data, len(MAGIC))[0]
    assert body_length == 5 + LABEL_BYTES + 7 + 50 + MODEL_COUNT * 44
    payload = data[len(MAGIC) + 4 : -2]
    usage_at = 5 + LABEL_BYTES + 7
    assert struct.unpack_from("<IIIIIQQIIIBB", payload, usage_at) == (
        17_122_315,
        102_887_685,
        2895,
        1257,
        33_970_000,
        68_000_000,
        919_546,
        12_483,
        428,
        500_000,
        0,
        2,
    )
    model_at = usage_at + 50
    name, quota, prompt, completion, requests = struct.unpack_from(
        "<20sIQQI", payload, model_at
    )
    assert name.rstrip(b"\0") == b"gpt-5.6-sol"
    assert (quota, prompt, completion, requests) == (
        20_000_000, 50_000_000, 700_000, 900
    )


def test_packet_rejects_more_than_four_models():
    try:
        packet(
            1,
            state="idle",
            labels=bytes(LABEL_BYTES),
            models=[{"name": str(index)} for index in range(MODEL_COUNT + 1)],
        )
    except ValueError as exc:
        assert "Too many" in str(exc)
    else:
        raise AssertionError("packet accepted too many model rows")


def test_heartbeat_packet_stays_small():
    assert len(packet(4)) == len(MAGIC) + 4 + 1 + 2


def test_packet_encodes_all_theme_values():
    theme_offset = 5 + LABEL_BYTES
    for sequence, (theme, value) in enumerate((
        ("dark", 0), ("light", 1), ("beach", 2), ("pixel", 3),
        ("mechanical", 4), ("paper", 5), ("glass", 6), ("vangogh", 7),
    ), start=7):
        data = packet(sequence, state="idle", labels=bytes(LABEL_BYTES), theme=theme)
        payload = data[len(MAGIC) + 4 : -2]
        assert payload[theme_offset] == value


def test_packet_rejects_unknown_theme():
    try:
        packet(1, state="idle", labels=bytes(LABEL_BYTES), theme="unknown")
    except ValueError as exc:
        assert "Invalid theme" in str(exc)
    else:
        raise AssertionError("packet accepted an unknown theme")


def test_rotation_mode_packet_is_small_bounded_and_crc_protected():
    for sequence, (mode, value) in enumerate((("off", 0), ("active", 1), ("all", 2))):
        data = rotation_packet(sequence, mode)
        assert data[4:10] == bytes((2, 0, sequence, 0, 9, value))
        assert struct.unpack_from("<H", data, len(data) - 2)[0] != 0
    try:
        rotation_packet(1, "sometimes")
    except ValueError as exc:
        assert "rotation" in str(exc)
    else:
        raise AssertionError("rotation packet accepted an unknown mode")
