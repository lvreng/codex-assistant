"""Append verified Astra layers to an existing SD card, retaining every original loop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))
from media_format import inspect_clip
from media_upload import ASTRA_FILES


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def install(source, card):
    if not os.path.ismount(card):
        raise ValueError("Destination is not a mounted SD card")
    manifest = json.loads((source / "layers.json").read_text())
    expected = {entry["file"]: entry for entry in manifest["clips"]}
    before = {}
    for name in ("C06.CJP", "C07.CJP"):
        with (card / name).open("rb") as file:
            info = inspect_clip(file)
        if info["frames"] != manifest["frames"] or info["fps"] != manifest["fps"]:
            raise ValueError("New layer timing differs from existing Astra loops")
        before[name] = digest(card / name)
    total = sum((source / name).stat().st_size for name in ASTRA_FILES)
    if shutil.disk_usage(card).free < total + 4*1024**2:
        raise ValueError("Not enough free space; original files will not be removed")
    started = time.monotonic()
    done = 0
    for name in ASTRA_FILES:
        path = source / name
        if digest(path) != expected[name]["sha256"]:
            raise ValueError(f"Source checksum mismatch: {name}")
        if (card / name).exists():
            if digest(card / name) == expected[name]["sha256"]:
                done += path.stat().st_size
                continue
            raise ValueError(f"A different {name} already exists; retain it for explicit backup first")
        temporary = card / (name + ".NEW")
        with path.open("rb") as src, temporary.open("wb") as dst:
            while chunk := src.read(2*1024**2):
                dst.write(chunk)
                done += len(chunk)
                elapsed = time.monotonic()-started
                print(f"SD [{'#'*int(20*done/total):.<20}] {done/total:.1%} | "
                      f"{done}/{total} bytes | elapsed {elapsed:.1f}s | "
                      f"ETA {elapsed*(total-done)/done:.1f}s", flush=True)
            dst.flush()
            os.fsync(dst.fileno())
        print(f"Verifying {name}", flush=True)
        if digest(temporary) != expected[name]["sha256"]:
            raise ValueError(f"SD verification failed: {name}")
        temporary.replace(card / name)
    for name, checksum in before.items():
        if digest(card / name) != checksum:
            raise ValueError(f"Original loop changed unexpectedly: {name}")
    print("SD layers verified; original loops unchanged. Unmount before removal.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("card", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT / "output/astra-layers")
    args = parser.parse_args()
    install(args.source, args.card)
