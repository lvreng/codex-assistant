import binascii
import io

import pytest

from media_format import (CLIPS, ENTRY, HEADER, TRANSITIONS, clip_name, header,
                          inspect_clip, transition_header, transition_name)


def make_clip(clip_id=0, frames=(b"a", b"bc"), fps=24):
    index_offset = HEADER.size + len(frames) * ENTRY.size
    entries = []
    offset = index_offset
    for frame in frames:
        entries.append(ENTRY.pack(offset, len(frame), binascii.crc32(frame)))
        offset += len(frame)
    index = b"".join(entries)
    data = header(clip_id, fps, len(frames), binascii.crc32(index))
    return data + index + b"".join(frames)


def make_transition(source=1, target=2, frames=(b"a", b"bc"), fps=24):
    index_offset = HEADER.size + len(frames) * ENTRY.size
    entries = []
    offset = index_offset
    for frame in frames:
        entries.append(ENTRY.pack(offset, len(frame), binascii.crc32(frame)))
        offset += len(frame)
    index = b"".join(entries)
    data = transition_header(source, target, fps, len(frames), binascii.crc32(index))
    return data + index + b"".join(frames)


def make_t2_transition(source=1, target=2, target_clip=2, frames=(b"a", b"bc"), fps=24):
    index_offset = HEADER.size + len(frames) * ENTRY.size
    entries = []
    offset = index_offset
    for frame in frames:
        entries.append(ENTRY.pack(offset, len(frame), binascii.crc32(frame)))
        offset += len(frame)
    index = b"".join(entries)
    data = transition_header(source, target, fps, len(frames), binascii.crc32(index),
                             target_clip=target_clip)
    return data + index + b"".join(frames)


@pytest.mark.parametrize("source,target", TRANSITIONS)
def test_all_transition_metadata_roundtrip(source, target):
    info = inspect_clip(io.BytesIO(make_transition(source, target)), verify_frames=True)

    assert transition_name(source, target) == f"T{source}{target}.CJP"
    assert info["source"] == source
    assert info["key"] == target
    assert info["transition"] is True
    assert info["transition_version"] == 1


@pytest.mark.parametrize("source,target,target_clip", [
    (source, target, target_clip)
    for source, target in TRANSITIONS for target_clip in range(len(CLIPS))
    if CLIPS[target_clip][1] == ("sol", "terra", "luna", "astra")[target - 1]
])
def test_all_t2_transition_metadata_roundtrip(source, target, target_clip):
    info = inspect_clip(io.BytesIO(make_t2_transition(source, target, target_clip)), verify_frames=True)

    assert transition_name(source, target, target_clip=target_clip) == f"T{source}{target}{target_clip}.CJP"
    assert info["transition_version"] == 2
    assert info["target_clip"] == target_clip
    assert info["entry_frame"] == 0
    assert info["working"] == int(CLIPS[target_clip][2])
    assert info["style"] == int(CLIPS[target_clip][3] == "linked")


def test_transition_header_rejects_source_target_and_upper_source_byte():
    with pytest.raises(ValueError, match="Invalid transition"):
        transition_header(1, 1, 24, 2, 0)
    payload = bytearray(make_transition())
    payload[19] = 1
    payload[28:32] = binascii.crc32(payload[:28]).to_bytes(4, "little")
    with pytest.raises(ValueError, match="Invalid clip header"):
        inspect_clip(io.BytesIO(payload))


def test_t2_rejects_mismatched_target_clip_and_nonzero_entry_frame():
    with pytest.raises(ValueError, match="Invalid target clip"):
        transition_header(1, 2, 24, 2, 0, target_clip=0)
    with pytest.raises(ValueError, match="Invalid entry frame"):
        transition_header(1, 2, 24, 2, 0, target_clip=2, entry_frame=1)


def test_t2_malformed_target_metadata_is_rejected():
    payload = bytearray(make_t2_transition())
    payload[15] = 0
    payload[28:32] = binascii.crc32(payload[:28]).to_bytes(4, "little")
    with pytest.raises(ValueError, match="Invalid clip header"):
        inspect_clip(io.BytesIO(payload))


def test_transition_index_corruption_is_rejected():
    payload = bytearray(make_transition())
    payload[HEADER.size] ^= 0x01

    with pytest.raises(ValueError, match="Invalid clip index"):
        inspect_clip(io.BytesIO(payload))


def test_transition_frame_crc_failure_is_rejected():
    payload = bytearray(make_transition())
    payload[HEADER.size + 2 * ENTRY.size] ^= 0xFF

    inspect_clip(io.BytesIO(payload))
    with pytest.raises(ValueError, match="Invalid frame checksum"):
        inspect_clip(io.BytesIO(payload), verify_frames=True)


@pytest.mark.parametrize("clip_id", range(len(CLIPS)))
def test_all_clip_metadata_ids_roundtrip_tiny_frames(clip_id):
    payload = make_clip(clip_id)

    info = inspect_clip(io.BytesIO(payload), verify_frames=True)

    assert clip_name(clip_id) == f"C{clip_id:02d}.CJP"
    assert info["frames"] == 2
    assert info["fps"] == 24
    assert info["bytes"] == len(payload)
    assert info["key"] == ("sol", "terra", "luna", "astra").index(CLIPS[clip_id][1]) + 1
    assert info["working"] == int(CLIPS[clip_id][2])
    assert info["style"] == int(CLIPS[clip_id][3] == "linked")


def test_verify_frames_rejects_crc_failure():
    payload = bytearray(make_clip())
    frame_offset = HEADER.size + 2 * ENTRY.size
    payload[frame_offset] ^= 0xFF

    inspect_clip(io.BytesIO(payload))
    with pytest.raises(ValueError, match="Invalid frame checksum"):
        inspect_clip(io.BytesIO(payload), verify_frames=True)


def test_header_corruption_is_rejected():
    payload = bytearray(make_clip())
    payload[0] ^= 0x01

    with pytest.raises(ValueError, match="Invalid clip header"):
        inspect_clip(io.BytesIO(payload))


def test_index_corruption_is_rejected():
    payload = bytearray(make_clip())
    payload[HEADER.size] ^= 0x01

    with pytest.raises(ValueError, match="Invalid clip index"):
        inspect_clip(io.BytesIO(payload))


def test_out_of_bounds_frame_offset_is_rejected():
    payload = bytearray(make_clip())
    index_start = HEADER.size
    index_end = index_start + 2 * ENTRY.size
    index = bytearray(payload[index_start:index_end])
    _, length, checksum = ENTRY.unpack(index[:ENTRY.size])
    index[:ENTRY.size] = ENTRY.pack(0, length, checksum)
    payload[:HEADER.size] = header(0, 24, 2, binascii.crc32(index))
    payload[index_start:index_end] = index

    with pytest.raises(ValueError, match="Invalid frame bounds"):
        inspect_clip(io.BytesIO(payload))


def test_truncated_frame_data_is_rejected():
    payload = bytearray(make_clip())
    del payload[-1]

    with pytest.raises(ValueError, match="Invalid frame bounds"):
        inspect_clip(io.BytesIO(payload))


def test_trailing_frame_data_is_rejected():
    payload = bytearray(make_clip())
    payload.extend(b"trailing")

    with pytest.raises(ValueError, match="Trailing or missing clip data"):
        inspect_clip(io.BytesIO(payload))


@pytest.mark.parametrize("fps,count", [(0, 2), (61, 2), (24, 1), (24, 3601)])
def test_invalid_timing_is_rejected(fps, count):
    with pytest.raises(ValueError, match="Invalid clip timing"):
        header(0, fps, count, 0)


@pytest.mark.parametrize("clip_id", [-1, len(CLIPS)])
def test_invalid_file_names_are_rejected(clip_id):
    with pytest.raises(ValueError, match="Invalid clip ID"):
        clip_name(clip_id)
