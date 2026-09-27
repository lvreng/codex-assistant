"""Opt-in local Astra timing test; stop the bridge and close the effects menu first."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from sd_camera_smoke import Device, ROOT, fields
from protocol import astra_period_packet, model_packet


class TimingDevice(Device):
    def __init__(self, *args, minimum_fps=22, samples=3):
        super().__init__(*args)
        self.timing = []
        self.minimum_fps = minimum_fps
        self.samples = samples

    def pump(self):
        offset = len(self.lines)
        super().pump()
        for line in self.lines[offset:]:
            if line.startswith("@ASTRA_TIMING "):
                self.timing.append({key: int(value) for key, value in fields(line).items()})

    def measure(self, style, minimum):
        offset = len(self.timing)
        previous = self.timing[-1]
        self.send(astra_period_packet, style, minimum)
        expected = (50 if style == 0 else 100) / minimum

        def matching():
            return [item for item in self.timing[offset:]
                    if item["style"] == style and item["min_x10"] == minimum and
                    item["clip"] == 6 + style and
                    item.get("layers") == 1 and
                    abs(item["rate_milli"] / 1000 - expected) < .001]

        self.until(lambda: len(matching()) >= self.samples, self.samples*2 + 6,
                   "Period change did not reach local playback")
        first, last = matching()[0], matching()[self.samples-1]
        if first["epoch"] != last["epoch"]:
            raise RuntimeError("Playback restarted during a stable period")
        if previous["clip"] == first["clip"]:
            if (previous["epoch"] != first["epoch"] or
                    first["source_milli"] < previous["source_milli"]):
                raise RuntimeError("Period change reset the current playback phase")
        seconds = (last["uptime_ms"] - first["uptime_ms"]) / 1000
        source_fps = (last["source_milli"] - first["source_milli"]) / 1000 / seconds
        display_fps = (last["output"] - first["output"]) / seconds
        background_fps = (last["bg_milli"] - first["bg_milli"]) / 1000 / seconds
        output_frames = last["output"] - first["output"]
        if abs(background_fps / 30 - 1) > .07:
            raise RuntimeError(f"Background was retimed by a light period change: {background_fps}")
        if abs(source_fps / (30 * expected) - 1) > .07:
            raise RuntimeError(f"Wrong source-frame speed: {source_fps}, expected {30 * expected}")
        if display_fps < self.minimum_fps:
            raise RuntimeError(f"Local playback is too slow: {display_fps:.2f} FPS, "
                               f"minimum {self.minimum_fps:.2f}")
        if expected < 1 and last["blends"] <= first["blends"]:
            raise RuntimeError("Slow playback did not interpolate frames")
        return dict(style=style, minimum=minimum / 10, maximum=last["max_x10"] / 10,
                    expected_speed=expected, source_fps=round(source_fps, 2),
                    output_fps=round(display_fps, 2), epoch=last["epoch"],
                    background_source_fps=round(background_fps, 2),
                    compose_max_ms=last["compose_max_us"] / 1000,
                    blend_max_ms=last["blend_max_us"] / 1000,
                    measurement_seconds=round(seconds, 3),
                    simd_pixels=last.get("simd_pixels", 0),
                    frame_exchange=last.get("frame_exchange", 0),
                    compose_mean_ms=round((last["compose_total_us"] - first["compose_total_us"])
                                         / output_frames / 1000, 3),
                    read_mean_ms=round((last["read_total_us"] - first["read_total_us"])
                                       / output_frames / 1000, 3),
                    read_mean_includes_crc=True,
                    jpeg_mean_ms=round((last["jpeg_total_us"] - first["jpeg_total_us"])
                                       / output_frames / 1000, 3),
                    crc_mean_ms=round((last["crc_total_us"] - first["crc_total_us"])
                                      / output_frames / 1000, 3))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--output", type=Path, default=ROOT / "output/astra-layers/device-test")
    parser.add_argument("--minimum-fps", type=float, default=22)
    parser.add_argument("--samples", type=int, default=3,
                        help="Stable telemetry samples per case (one every two seconds)")
    args = parser.parse_args()
    if args.samples < 3:
        parser.error("--samples must be at least 3")
    args.output.mkdir(parents=True, exist_ok=True)
    result = dict(passed=False, usb_artwork_sent=0, measurements=[])
    with (args.output / "serial.log").open("w") as log:
        device = TimingDevice(args.port, log, minimum_fps=args.minimum_fps, samples=args.samples)
        original = None
        try:
            device.until(lambda: device.hello and device.media and device.stats and device.timing,
                         20, "Device/SD/timing telemetry not ready")
            original = device.timing[-1].copy()
            if device.stats[-1]["theme"] != "8":
                raise RuntimeError("Select Thinker physically and close the effects menu")
            device.send(model_packet, 4)
            device.until(lambda: device.timing[-1]["clip"] in (6, 7) and
                         device.timing[-1]["output"] > original["output"],
                         10, "Astra working clip did not start")
            began = time.monotonic()
            cases = [(1, 100), (1, 200), (1, 50), (1, 800), (0, 50), (0, 100)]
            for count, (style, minimum) in enumerate(cases, 1):
                measured = device.measure(style, minimum)
                result["measurements"].append(measured)
                elapsed = time.monotonic() - began
                print(f"Timing [{'#' * count}{'.' * (len(cases)-count)}] {count/len(cases):.0%} | "
                      f"{count}/{len(cases)} | elapsed {elapsed:.1f}s | "
                      f"ETA {elapsed/count*(len(cases)-count):.1f}s | {measured}", flush=True)
            device.transition(4, 1)
            device.transition(1, 4)
            result["handoffs"] = device.handoffs
            device.state = "done"
            device.next_heartbeat = 0
            device.wait(3)
            offset = len(device.timing)
            device.send(astra_period_packet, 0, 200)
            device.until(lambda: len(device.timing) >= offset + 3, 8, "Missing completion telemetry")
            first, last = device.timing[-3], device.timing[-1]
            if first["output"] != last["output"]:
                raise RuntimeError("Working-state time scaling remained active after completion")
            result.update(passed=True, completed_state_isolated=True,
                          presentation=device.presentation[-1], usb_artwork_received=0)
        finally:
            try:
                if original:
                    for style, key in ((1, "linked_min_x10"), (0, "classic_min_x10")):
                        device.send(astra_period_packet, style, original[key])
                        device.wait(.2)
                    style = original["style"]
                    device.send(astra_period_packet, style, original["min_x10"])
                    device.until(lambda: device.timing[-1]["style"] == style and
                                 all(device.timing[-1][key] == original[key]
                                     for key in ("classic_min_x10", "linked_min_x10")),
                                 5, "Original period settings were not restored")
                    device.wait(1.2)
                    result["restored"] = original
            finally:
                device.serial.close()
                (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
