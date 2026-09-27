#!/usr/bin/env python3
"""Render isolated fixtures and compare every pixel with the firmware preview."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "host"))
from codex_state import Session
from protocol import packet
from text_labels import labels
from renderer import Renderer

THEMES = ("dark", "light", "beach", "pixel", "mechanical", "paper", "glass", "vangogh")


def fixture(theme):
    sessions = [Session("one", "桌面助手开发", "桌面助手开发", "thinking"),
                Session("two", "交互动画验证", "交互动画验证", "done"),
                Session("three", "主题效果检查", "主题效果检查", "working")]
    return dict(state="thinking", total=3, duration_seconds=156,
                labels=labels(sessions[0], sessions), theme=theme,
                balance_quota=11755000, spent_quota=4085000,
                today_quota=820000, today_request_count=48,
                today_prompt_tokens=39281, today_completion_tokens=5640,
                input_tps_x10=12800, output_tps_x10=420, usage_stale=False,
                models=[dict(name=name, quota=640000 if i == 0 else 170000 // i,
                             prompt_tokens=9000 // (i + 1),
                             completion_tokens=1300 // (i + 1), request_count=24 // (i + 1))
                        for i, name in enumerate(("GPT-5", "GPT-5 mini", "GPT-4.1", "o4-mini"))])


def image(renderer):
    values = np.frombuffer(renderer.buffer, dtype=np.uint16).reshape(480, 800)
    rgb = np.stack((((values >> 11) & 31) * 255 // 31,
                    ((values >> 5) & 63) * 255 // 63,
                    (values & 31) * 255 // 31), axis=-1).astype(np.uint8)
    return Image.fromarray(rgb)


def render_one(theme, output):
    renderer = Renderer()
    view = fixture(theme)
    renderer.feed(packet(1, **view))
    for _ in range(187):
        renderer.tick(16)
    screenshot = image(renderer)
    screenshot.save(output / f"{theme}.png")
    label_path = output / f"{theme}.labels"
    label_path.write_bytes(view["labels"])
    reference_path = output / f"{theme}.ppm"
    subprocess.run([str(PROJECT / "build/desktop/pet_preview"), theme, "1", "3",
                    str(label_path), str(reference_path)],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    reference = Image.open(reference_path)
    difference = np.any(np.asarray(screenshot) != np.asarray(reference), axis=-1)
    mismatched = int(difference.sum())
    if mismatched:
        raise AssertionError(f"{theme}: {mismatched} pixels differ from firmware preview")
    label_path.unlink()
    reference_path.unlink()
    renderer.native.pet_details(1)
    for _ in range(60):
        renderer.tick(16)
    image(renderer).save(output / f"{theme}-details.png")
    print(f"{theme}: 384000/384000 pixels identical", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one", choices=THEMES)
    parser.add_argument("--output", type=Path, default=PROJECT / "output/desktop/parity")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.one:
        render_one(args.one, args.output)
        return
    started = time.monotonic()
    for index, theme in enumerate(THEMES, 1):
        result = subprocess.run([sys.executable, __file__, "--one", theme,
                                 "--output", str(args.output)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            print(result.stdout + result.stderr)
            raise SystemExit(result.returncode)
        elapsed = time.monotonic() - started
        eta = elapsed / index * (len(THEMES) - index)
        print(f"Pixel parity [{'#' * index}{'.' * (8-index)}] {index/8:.0%} | "
              f"{index}/8 | elapsed {elapsed:.1f}s | ETA {eta:.1f}s | {theme}: identical",
              flush=True)
    overview = Image.new("RGB", (1280, 832), "#171c22")
    draw = ImageDraw.Draw(overview)
    for index, theme in enumerate(THEMES):
        x, y = (index % 2) * 640, (index // 2) * 208
        with Image.open(args.output / f"{theme}.png") as source:
            overview.paste(source.resize((320, 192)), (x, y + 16))
        with Image.open(args.output / f"{theme}-details.png") as source:
            overview.paste(source.resize((320, 192)), (x + 320, y + 16))
        draw.text((x + 8, y + 2), theme, fill="#ffffff")
    overview.save(args.output / "overview.png")


if __name__ == "__main__":
    main()
