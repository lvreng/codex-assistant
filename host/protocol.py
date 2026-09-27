import binascii
import struct
from codex_state import STATES
from settings import validate_expiry

MAGIC = b"\xC0\xDEPT"
LABEL_BYTES = 2240
MODEL_COUNT = 4
MODEL_NAME_BYTES = 20
ACCOUNT_FORMAT = "<BBBH16s20s"
ACCOUNT_KEYS = ("api_source", "api_mode", "account_kind", "week_remaining_x10", "week_reset", "plan_name", "membership_expires_at")


def disconnect_packet(sequence):
    framed = struct.pack("<HHB", 1, sequence & 0xFFFF, 8)
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def model_picker_packet(sequence, status, mask, current, context, request, title):
    from text_labels import mask as text_mask
    if not (0 <= status <= 7 and 0 <= mask <= 63 and 0 <= current <= 6 and
            0 <= context <= 0xFFFFFFFF and 0 <= request <= 65535):
        raise ValueError("Invalid model selection status")
    bitmap = text_mask(title, align="left")
    title = str(title).encode("utf-8")[:47].decode("utf-8", "ignore").encode("utf-8")
    body = struct.pack("<BBBBIH48s", 7, status, mask, current, context, request, title) + bitmap
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def model_test_packet(sequence, start):
    if type(start) is not bool:
        raise ValueError("Model test requires a boolean")
    body = bytes((6, int(start)))
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def model_packet(sequence, model_key):
    if not 0 <= int(model_key) <= 4:
        raise ValueError("Invalid model key")
    body = bytes((5, int(model_key)))
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def rotation_packet(sequence, mode):
    if mode not in ("off", "active", "all"):
        raise ValueError("Invalid conversation rotation mode")
    body = bytes((9, ("off", "active", "all").index(mode)))
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def astra_period_packet(sequence, style, minimum):
    """Set local SD timing in deciseconds, without sending artwork or changing the model."""
    if type(style) is not int or style not in (0, 1) or type(minimum) is not int or not 10 <= minimum <= 800:
        raise ValueError("Invalid local Astra period")
    body = struct.pack("<BBH", 10, style, minimum)
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))


def packet(sequence, state=None, total=0, index=0, pending=0, labels=b"", theme="dark",
           transition_ms=650, duration_seconds=0,
           balance_quota=0xFFFFFFFF, spent_quota=0xFFFFFFFF,
           request_count=0xFFFFFFFF, today_request_count=0xFFFFFFFF,
           today_quota=0xFFFFFFFF, today_prompt_tokens=0xFFFFFFFFFFFFFFFF,
           today_completion_tokens=0xFFFFFFFFFFFFFFFF, input_tps_x10=0,
           output_tps_x10=0, quota_per_unit=500_000, models=(), usage_stale=True,
           api_source=None, api_mode="auto", account_kind=0,
           week_remaining_x10=65535, week_reset="", plan_name="", membership_expires_at=None):
    if state is None:
        body = b"\x02"
    else:
        if state not in STATES or len(labels) != LABEL_BYTES:
            raise ValueError("Invalid state or label dimensions")
        if not 0 <= total <= 255 or not (index == 0 if total == 0 else 0 <= index < total) or not 0 <= pending <= total:
            raise ValueError("Invalid session counters")
        if theme not in ("dark", "light", "beach", "pixel", "mechanical", "paper", "glass", "vangogh") or type(transition_ms) is not int or not 200<=transition_ms<=2000:
            raise ValueError("Invalid theme or transition duration")
        u32 = (balance_quota, spent_quota, request_count, today_request_count,
               today_quota, input_tps_x10, output_tps_x10, quota_per_unit)
        if any(not 0 <= value <= 0xFFFFFFFF for value in u32):
            raise ValueError("Invalid MoreCode telemetry")
        if any(not 0 <= value <= 0xFFFFFFFFFFFFFFFF
               for value in (today_prompt_tokens, today_completion_tokens)):
            raise ValueError("Invalid token count")
        if len(models) > MODEL_COUNT:
            raise ValueError("Too many model telemetry rows")
        body = bytes((1, STATES.index(state), total, index, pending)) + labels
        if not 0 <= duration_seconds <= 0xFFFFFFFF:
            raise ValueError("Invalid session duration")
        body += struct.pack("<BHI", {"dark": 0, "light": 1, "beach": 2, "pixel": 3,
                                      "mechanical": 4, "paper": 5, "glass": 6,
                                      "vangogh": 7}[theme],
                            transition_ms, duration_seconds)
        body += struct.pack(
            "<IIIIIQQIIIBB",
            balance_quota, spent_quota, request_count, today_request_count,
            today_quota, today_prompt_tokens, today_completion_tokens,
            input_tps_x10, output_tps_x10, quota_per_unit,
            int(bool(usage_stale)), len(models),
        )
        for index in range(MODEL_COUNT):
            model = models[index] if index < len(models) else {}
            name = str(model.get("name", "")).encode("ascii", "replace")[:MODEL_NAME_BYTES]
            name = name.ljust(MODEL_NAME_BYTES, b"\0")
            values = (
                int(model.get("quota", 0)),
                int(model.get("prompt_tokens", 0)),
                int(model.get("completion_tokens", 0)),
                int(model.get("request_count", 0)),
            )
            if (not 0 <= values[0] <= 0xFFFFFFFF or
                    not 0 <= values[1] <= 0xFFFFFFFFFFFFFFFF or
                    not 0 <= values[2] <= 0xFFFFFFFFFFFFFFFF or
                    not 0 <= values[3] <= 0xFFFFFFFF):
                raise ValueError("Invalid model telemetry")
            body += struct.pack("<20sIQQI", name, *values)
        if api_source is not None:
            if api_source not in ("official", "morecode") or api_mode not in ("auto", "official", "morecode"):
                raise ValueError("Invalid API source")
            if account_kind not in (0, 1, 2) or not (0 <= week_remaining_x10 <= 1000 or week_remaining_x10 == 65535):
                raise ValueError("Invalid official telemetry")
            body += struct.pack(ACCOUNT_FORMAT, 1 if api_source == "official" else 2,
                                ("auto", "official", "morecode").index(api_mode), account_kind,
                                week_remaining_x10, str(week_reset).encode("ascii", "replace")[:15],
                                str(plan_name).encode("ascii", "replace")[:19])
            if membership_expires_at is not None:
                body += struct.pack("<17s", validate_expiry(membership_expires_at).encode("ascii"))
    framed = struct.pack("<HH", len(body), sequence & 0xFFFF) + body
    return MAGIC + framed + struct.pack("<H", binascii.crc_hqx(framed, 0xFFFF))
