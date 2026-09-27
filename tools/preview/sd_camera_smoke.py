"""Opt-in SD-only device test. Stop the bridge and select Thinker before running."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))
from protocol import LABEL_BYTES, model_packet, model_test_packet, packet

# An Euler circuit covers every directed pair exactly once.
ROUTE = (1, 2, 1, 3, 1, 4, 2, 3, 2, 4, 3, 4, 1)


def fields(line):
    return dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)


def validate_rotation_checks(presentation):
    if not presentation:
        raise RuntimeError("Missing tear-free presenter or rotation check")
    stat = presentation[-1]
    partial = int(stat.get("partial_checks", 0))
    # One full-frame check, then one full-image check per partial-update buffer.
    if not 0 <= partial <= 3 or int(stat.get("rotation_checked", 0)) != 480*800*(1+partial):
        raise RuntimeError("Incomplete presenter rotation checks")


class Device:
    def __init__(self, port, log):
        self.serial = serial.Serial(port=None, baudrate=115200, timeout=.025,
                                    write_timeout=3, exclusive=True)
        # Match bridge.Connection: both deasserted resets native P4 USB/JTAG.
        self.serial.dtr = self.serial.rts = True
        self.serial.port = port
        self.serial.open()
        self.log = log
        self.started = time.monotonic()
        self.sequence = 0
        self.next_heartbeat = 0
        self.state = "thinking"
        self.pending = b""
        self.lines = []
        self.stats = []
        self.camera = []
        self.resumes = []
        self.handoffs = []
        self.landing_frame = None
        self.presentation = []
        self.hello = self.media = False

    def send(self, factory, *args, **kwargs):
        data = factory(self.sequence, *args, **kwargs)
        self.serial.write(data)
        self.sequence += 1

    def pump(self):
        now = time.monotonic()
        if self.hello and now >= self.next_heartbeat:
            self.send(packet, self.state, total=0, labels=bytes(LABEL_BYTES), theme="glass")
            self.next_heartbeat = now + 2
        self.pending += self.serial.read(max(1, self.serial.in_waiting))
        while b"\n" in self.pending:
            raw, self.pending = self.pending.split(b"\n", 1)
            line = raw.decode("utf-8", "replace").strip()
            elapsed = time.monotonic() - self.started
            self.log.write(f"{elapsed:.3f} {line}\n")
            self.log.flush()
            self.lines.append(line)
            if any(error in line for error in ("Guru Meditation", "assert failed", "panic'ed",
                                                "SD local media disabled", "SD camera clip missing",
                                                "SD mount failed", "invalid frame in")):
                raise RuntimeError(line)
            if line.startswith("@PET_HELLO"):
                self.hello = True
            if line.startswith("@MEDIA_HELLO"):
                self.media = True
            if line.startswith("@CAMERA "):
                event = fields(line)
                event.update(event=line.split()[1], seconds=elapsed)
                self.camera.append(event)
                print(line, flush=True)
            if line.startswith("@PRESENT "):
                stat = fields(line)
                if int(stat["errors"]) or int(stat["rotation_errors"]) or int(stat.get("partial_errors", 0)):
                    raise RuntimeError("Frame presentation/rotation failed: " + line)
                self.presentation.append(stat)
            if line.startswith("@LANDING_FRAME "):
                self.landing_frame = fields(line)
                if self.landing_frame["pixel_equal"] != "1":
                    raise RuntimeError("Final frame differs from the retained landing image: " + line)
            if line.startswith("@LOOP_HANDOFF "):
                handoff = fields(line)
                end = self.landing_frame
                if (handoff["pixel_equal"] != "1" or handoff["ordered"] != "1" or not end or
                        end["crc"] != handoff["crc"] or
                        (int(end["sequence"]) + 1) % 2**32 != int(handoff["sequence"])):
                    raise RuntimeError("First loop image is not identical to the displayed final frame: " + line)
                self.handoffs.append(handoff)
                print(line, flush=True)
            if line.startswith("@LOOP_RESUME "):
                resume = fields(line)
                if resume.get("continuous") != "1":
                    raise RuntimeError("Inexact post-camera state join: " + line)
                if resume["joined"] == "0" and (resume["clip"] != resume["expected_clip"] or
                                                resume["frame"] != resume["expected_frame"]):
                    raise RuntimeError("Landing reset its clip or cursor: " + line)
                self.resumes.append(resume)
                print(line, flush=True)
            if line.startswith("@ART_STAT "):
                stat = fields(line)
                if int(stat["frames"]) or int(stat["bad"]):
                    raise RuntimeError("USB artwork received or decoder reported errors: " + line)
                if self.stats and float(stat["uptime_s"]) <= float(self.stats[-1]["uptime_s"]):
                    raise RuntimeError("Device rebooted")
                self.stats.append(stat)
                print(line, flush=True)
            if line.startswith("@STAT ") and int(fields(line)["crc_bad"]):
                raise RuntimeError("Serial CRC errors: " + line)

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pump()

    def until(self, condition, seconds, message):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.pump()
            if condition():
                return
        raise RuntimeError(message)

    def transition(self, source, target, late_state=None):
        offset = len(self.camera)
        resumes = len(self.resumes)
        handoffs = len(self.handoffs)
        self.send(model_packet, target)
        if late_state:
            self.until(lambda: any(e["event"] == "start" for e in self.camera[offset:]), 2,
                       "Camera did not start")
            self.wait(1.70)
            self.state = late_state
            self.next_heartbeat = 0
        self.until(lambda: any(e["event"] == "end" and int(e["from"]) == source and
                               int(e["to"]) == target for e in self.camera[offset:]),
                   8, f"SD camera {source}->{target} did not complete")
        events = [e for e in self.camera[offset:] if int(e["from"]) == source and int(e["to"]) == target]
        if ([e["event"] for e in events] != ["start", "end"] or
                events[0].get("asset_version") != "2" or int(events[-1]["frames"]) != 56):
            raise RuntimeError(f"Unexpected camera sequence: {events}")
        duration = events[-1]["seconds"] - events[0]["seconds"]
        if events[-1].get("exact") != "1" or not 1800 <= int(events[-1]["elapsed_ms"]) <= 2250:
            raise RuntimeError(f"Inexact or slow landing: {events[-1]}")
        self.until(lambda: len(self.resumes) > resumes, 1, "No continuous loop handoff")
        self.until(lambda: len(self.handoffs) > handoffs, 1, "No pixel-identical displayed handoff")
        if self.resumes[-1].get("decoded_entry") != "1" or self.resumes[-1]["frame"] != "0":
            raise RuntimeError("Landing was not decoded from the actual loop frame zero")
        return dict(source=source, target=target, frames=56, observed_seconds=round(duration, 3),
                    output_frames=int(events[-1]["output_frames"]), handoff=self.handoffs[-1])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--button-test", action="store_true")
    parser.add_argument("--wait-theme", type=float, default=0,
                        help="Seconds to wait for physical selection of Thinker")
    parser.add_argument("--output", type=Path, default=ROOT / "output/sd-camera-v4/device-test")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"passed": False, "usb_artwork_sent": 0, "transitions": []}
    device = None
    try:
        with (args.output / "serial.log").open("w") as log:
            device = Device(args.port, log)
            device.until(lambda: device.hello and device.media and bool(device.stats), 20,
                         "Device/SD not ready; inspect startup logs and card, do not stream video")
            if device.stats[-1]["theme"] != "8":
                print("Waiting for physical Thinker selection; close effects menu", flush=True)
                device.until(lambda: device.stats[-1]["theme"] == "8", args.wait_theme,
                             "Select Thinker with the physical button and close effects menu")
            device.send(model_packet, 1)
            device.wait(5)
            if device.stats[-1]["active"] != "1":
                raise RuntimeError("SD artwork is not visible")
            if args.button_test:
                start = len(device.lines)
                device.send(model_test_packet, True)
                device.wait(28)
                reports = [fields(line) for line in device.lines[start:] if line.startswith("@MODEL_TEST ")]
                if not {1, 2, 3, 4} <= {int(report["model"]) for report in reports} or reports[-1]["active"] != "0":
                    raise RuntimeError("Four-model test button sequence failed")
                result["button_sequence"] = "passed"
            began = time.monotonic()
            for count, (source, target) in enumerate(zip(ROUTE, ROUTE[1:]), 1):
                # Also exercise completion-state handoff without changing the shot.
                device.state = "done" if count % 2 == 0 else "thinking"
                device.next_heartbeat = 0
                result["transitions"].append(device.transition(source, target))
                device.wait(.4)
                elapsed = time.monotonic() - began
                print(f"Device [{'#' * count}{'.' * (12-count)}] {count/12:.0%} | "
                      f"{count}/12 routes | elapsed {elapsed:.1f}s | "
                      f"ETA {elapsed/count*(12-count):.1f}s", flush=True)
            # Intermediate model changes must queue, never restart a half-finished shot.
            offset = len(device.camera)
            device.send(model_packet, 2)
            device.wait(.25)
            device.send(model_packet, 3)
            device.wait(.15)
            device.send(model_packet, 4)
            device.until(lambda: any(e["event"] == "end" and e["from"] == "2" and e["to"] == "4"
                                     for e in device.camera[offset:]), 10, "Rapid requests did not settle")
            ends = [(e["from"], e["to"]) for e in device.camera[offset:] if e["event"] == "end"]
            if ends != [("1", "2"), ("2", "4")]:
                raise RuntimeError(f"Rapid requests restarted a shot: {ends}")
            result["queued_latest"] = "passed"
            device.state = "thinking"
            device.next_heartbeat = 0
            device.wait(.6)
            device.transition(4, 1, late_state="done")
            device.wait(.6)
            device.transition(1, 4, late_state="thinking")
            device.wait(.6)
            result["late_state_handoff"] = "passed"
            device.state = "done"
            device.next_heartbeat = 0
            device.wait(12)
            if len(device.stats) < 2:
                raise RuntimeError("Insufficient local frame samples")
            validate_rotation_checks(device.presentation)
            active = [stat for stat in device.stats if stat["active"] == "1" and stat["theme"] == "8"]
            first, last = active[0], active[-1]
            frames = int(last["local_frames"]) - int(first["local_frames"])
            seconds = float(last["uptime_s"]) - float(first["uptime_s"])
            result.update(passed=True, local_frames=frames, measured_seconds=seconds,
                          local_decode_fps=round(frames / seconds, 2),
                          max_camera_decode_ms=float(last["transition_max_ms"]),
                          usb_artwork_received=sum(int(stat["frames"]) for stat in device.stats),
                          stats=device.stats, presentation=device.presentation,
                          resumes=device.resumes, handoffs=device.handoffs)
            print(json.dumps({k: v for k, v in result.items() if k not in ("stats", "transitions", "presentation", "resumes", "handoffs")},
                             indent=2), flush=True)
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        if device:
            device.serial.close()
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
