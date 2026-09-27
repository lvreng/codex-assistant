"""Versioned, indexed baseline-JPEG clips for the P4's SD player."""
import binascii
import struct

MAGIC = b"CJP4V1\0\0"
TRANSITION_MAGIC = b"CJP4T1\0\0"
TRANSITION_MAGIC_V2 = b"CJP4T2\0\0"
HEADER = struct.Struct("<8sHHHBBHHIII")
ENTRY = struct.Struct("<III")
WIDTH, HEIGHT = 560, 416
MAX_FRAME = 160 * 1024
MAX_FILE = 256 * 1024 * 1024
# id, key, working, style. Completed Astra is independent of relay style.
CLIPS = (
    (0, "sol", True, "linked"), (1, "sol", False, "linked"),
    (2, "terra", True, "linked"), (3, "terra", False, "linked"),
    (4, "luna", True, "linked"), (5, "luna", False, "linked"),
    (6, "astra", True, "classic"), (7, "astra", True, "linked"),
    (8, "astra", False, "linked"),
)
TRANSITIONS = tuple((source, target) for source in range(1, 5)
                    for target in range(1, 5) if source != target)


def clip_name(clip_id):
    if not 0 <= clip_id < len(CLIPS):
        raise ValueError("Invalid clip ID")
    return f"C{clip_id:02d}.CJP"


def header(clip_id, fps, count, index_crc):
    if not 1 <= fps <= 60 or not 2 <= count <= 3600:
        raise ValueError("Invalid clip timing")
    _, key, working, style = CLIPS[clip_id]
    data = HEADER.pack(MAGIC, WIDTH, HEIGHT, fps,
                       ("sol", "terra", "luna", "astra").index(key) + 1,
                       int(working), int(style == "linked"), 0, count,
                       index_crc, 0)
    return data[:-4] + struct.pack("<I", binascii.crc32(data[:-4]))


def transition_name(source, target, *, target_clip=None):
    if (source, target) not in TRANSITIONS:
        raise ValueError("Invalid transition")
    if target_clip is not None and (not 0 <= target_clip < len(CLIPS) or
            CLIPS[target_clip][1] != ("sol", "terra", "luna", "astra")[target - 1]):
        raise ValueError("Invalid target clip")
    return (f"T{source}{target}.CJP" if target_clip is None
            else f"T{source}{target}{target_clip}.CJP")


def transition_header(source, target, fps, count, index_crc, *, target_clip=None,
                     entry_frame=0):
    if (source, target) not in TRANSITIONS:
        raise ValueError("Invalid transition")
    if not 1 <= fps <= 60 or not 2 <= count <= 3600:
        raise ValueError("Invalid clip timing")
    if entry_frame != 0:
        raise ValueError("Invalid entry frame")
    if target_clip is None:
        magic, working, style = TRANSITION_MAGIC, 0, 0
    else:
        if not 0 <= target_clip < len(CLIPS) or CLIPS[target_clip][1] != ("sol", "terra", "luna", "astra")[target - 1]:
            raise ValueError("Invalid target clip")
        magic = TRANSITION_MAGIC_V2
        working = target_clip
        style = entry_frame
    data = HEADER.pack(magic, WIDTH, HEIGHT, fps, target,
                       working, style, source, count, index_crc, 0)
    return data[:-4] + struct.pack("<I", binascii.crc32(data[:-4]))


def inspect_clip(file, verify_frames=False):
    """Validate all offsets before permitting a decoder to see frame data."""
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    raw = file.read(HEADER.size)
    if len(raw) != HEADER.size:
        raise ValueError("Truncated clip header")
    magic, w, h, fps, key, working, style, reserved, count, crc, check = HEADER.unpack(raw)
    transition = magic in (TRANSITION_MAGIC, TRANSITION_MAGIC_V2)
    basic_invalid = (magic not in (MAGIC, TRANSITION_MAGIC, TRANSITION_MAGIC_V2) or (w, h) != (WIDTH, HEIGHT)
            or not 1 <= fps <= 60 or not 1 <= key <= 4 or not 2 <= count <= 3600
            or check != binascii.crc32(raw[:-4]) or size > MAX_FILE)
    if basic_invalid:
        raise ValueError("Invalid clip header")
    if (magic == TRANSITION_MAGIC_V2 and (working >= len(CLIPS) or style != 0)) or \
            (magic != TRANSITION_MAGIC_V2 and (working > 1 or style > 1)):
        raise ValueError("Invalid clip header")
    if transition:
        version = 2 if magic == TRANSITION_MAGIC_V2 else 1
        if reserved < 1 or reserved > 4 or reserved == key:
            raise ValueError("Invalid clip header")
        if version == 1 and (working or style):
            raise ValueError("Invalid clip header")
        source = reserved
    else:
        if reserved:
            raise ValueError("Invalid clip header")
        source = 0
    index = file.read(count * ENTRY.size)
    if len(index) != count * ENTRY.size or binascii.crc32(index) != crc:
        raise ValueError("Invalid clip index")
    entries = list(ENTRY.iter_unpack(index))
    end = HEADER.size + len(index)
    for offset, length, checksum in entries:
        if offset != end or not 1 <= length <= MAX_FRAME or offset + length > size:
            raise ValueError("Invalid frame bounds")
        end = offset + length
        if verify_frames:
            file.seek(offset)
            if binascii.crc32(file.read(length)) != checksum:
                raise ValueError("Invalid frame checksum")
    if end != size:
        raise ValueError("Trailing or missing clip data")
    target_clip = raw[15] if transition and magic == TRANSITION_MAGIC_V2 else None
    entry_frame = struct.unpack_from("<H", raw, 16)[0] if transition else 0
    if transition and magic == TRANSITION_MAGIC_V2:
        if not 0 <= target_clip < len(CLIPS) or CLIPS[target_clip][1] != ("sol", "terra", "luna", "astra")[key - 1]:
            raise ValueError("Invalid clip header")
        if style != 0:
            raise ValueError("Invalid entry frame")
        working = int(CLIPS[target_clip][2])
        style = int(CLIPS[target_clip][3] == "linked")
    return dict(fps=fps, key=key, working=working, style=style,
                frames=count, bytes=size, entries=entries, source=source,
                transition=transition, transition_version=(2 if magic == TRANSITION_MAGIC_V2 else 1 if transition else None),
                target_clip=target_clip, entry_frame=entry_frame)
