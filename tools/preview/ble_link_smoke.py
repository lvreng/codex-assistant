"""Opt-in real USB/BLE handover test; stop both bridge services first."""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))
from bridge import Connection
from protocol import LABEL_BYTES, model_packet


class Capture:
    def __init__(self, port, mode, records, log):
        self.inner, self.mode, self.records, self.log = port, mode, records, log
        self.pending = b""

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def read(self, size):
        data = self.inner.read(size)
        self.pending += data
        while b"\n" in self.pending:
            raw, self.pending = self.pending.split(b"\n", 1)
            line = raw.decode("utf8", "replace").strip()
            self.log.write(f"{time.monotonic():.3f} {self.mode} {line}\n")
            self.log.flush()
            if line.startswith("@BLE_EVENT"):
                print(line, flush=True)
            if any(error in line for error in ("Guru Meditation", "assert failed", "panic'ed")):
                raise RuntimeError(line)
            if line.startswith("@"):
                fields = dict(item.split("=", 1) for item in line.split()[1:] if "=" in item)
                self.records.append(dict(kind=line.split()[0], transport=self.mode, time=time.monotonic(), **fields))
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--output", type=Path, default=ROOT / "output/bluetooth/link-test")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"passed": False, "artwork_bytes_sent": 0}
    records = []
    connection = None
    started = time.monotonic()
    next_send = 0

    with (args.output / "serial.log").open("w") as log, tempfile.TemporaryDirectory(prefix="codex-ble-test-") as temp:
        def pump():
            nonlocal next_send
            now = time.monotonic()
            for key in ("_opening", "port"):
                port = getattr(connection, key)
                if port is not None and not isinstance(port, Capture):
                    setattr(connection, key, Capture(port, connection.transport, records, log))
            if connection._opening:
                connection.poll_open()
            if connection.port:
                connection.receive()
                if now >= next_send:
                    connection.send(dict(state="thinking", total=0, labels=bytes(LABEL_BYTES),
                                         theme="glass", duration_seconds=int(now-started)))
                    if connection.last_model != 4:
                        connection.sequence = (connection.sequence+1) & 0xffff
                        connection.port.write(model_packet(connection.sequence, 4))
                        connection.last_model = 4
                    next_send = now + .5
                connection.poll_transport(now)
            time.sleep(.02)

        def wait_until(check, seconds, message):
            deadline = time.monotonic() + seconds
            while not check():
                if time.monotonic() >= deadline:
                    raise RuntimeError(message)
                pump()

        def sample(seconds):
            start = time.monotonic()
            wait_until(lambda: time.monotonic()-start >= seconds, seconds+1, "sample timeout")

        def progress(step, message):
            print(f"Link [{'#'*step}{'.'*(4-step)}] {step}/4 | elapsed {time.monotonic()-started:.1f}s | {message}", flush=True)

        try:
            connection = Connection(args.port, transport="auto")
            connection.begin_open()
            wait_until(lambda: connection._pairing_done, 50, "USB registration/pairing failed")
            result["address"] = connection.bluetooth_address
            progress(1, "USB identity and encrypted pairing verified")
            connection.close(wait=True)
            absent_port = Path(temp) / "usb"
            connection = Connection(str(absent_port), transport="auto")
            ble_started = time.monotonic()
            connection.begin_open()
            wait_until(lambda: connection.port is not None, 35, "BLE handshake failed")
            result["ble_handshake_seconds"] = round(time.monotonic()-ble_started, 3)
            wait_until(lambda: any(r["kind"] == "@STAT" and r["transport"] == "ble"
                                   and r.get("link") == "ble" for r in records),
                       12, "Device did not accept BLE state packets")
            progress(2, "USB unavailable: real BLE data and device acknowledgments received")
            offset = len(records)
            sample(22)
            measured = records[offset:]
            identity = [r for r in measured if r["kind"] == "@BLE_ID"]
            if not identity or identity[-1].get("encrypted") != "1":
                raise RuntimeError("BLE encryption was not confirmed by the board")
            art = [r for r in measured if r["kind"] == "@ART_STAT"]
            if len(art) < 3:
                raise RuntimeError("Missing BLE local-frame telemetry")
            frames = int(art[-1]["local_frames"]) - int(art[0]["local_frames"])
            seconds = float(art[-1]["uptime_s"]) - float(art[0]["uptime_s"])
            fps = frames / seconds
            stats = [r for r in measured if r["kind"] == "@STAT"]
            if (fps < 29 or any(int(r["crc_bad"]) for r in stats) or
                    any(int(r["frames"]) or int(r["bad"]) for r in art) or
                    time.monotonic()-connection.last_ack > 3):
                raise RuntimeError("BLE frame rate, CRC, or acknowledgments regressed")
            result["ble"] = dict(local_frames=frames, seconds=seconds, local_fps=round(fps, 2),
                                 crc_bad=0, usb_artwork_frames=0, encrypted=True,
                                 mtu=int(identity[-1]["mtu"]), stats=stats[-1])
            progress(3, f"BLE local SD playback: {fps:.2f} FPS, zero CRC errors")
            restored = time.monotonic()
            absent_port.symlink_to(args.port)
            wait_until(lambda: connection.transport == "usb" and connection.port is not None,
                       15, "Automatic USB recovery failed")
            result["usb_recovery_seconds"] = round(time.monotonic()-restored, 3)
            offset = len(records)
            wait_until(lambda: any(r["kind"] == "@STAT" and r.get("link") == "usb" for r in records[offset:]),
                       12, "Device did not return to USB priority")
            progress(4, "USB restored: automatic handover and full state resync verified")
            result["passed"] = True
        finally:
            if connection:
                connection.close(wait=True)
            result["elapsed_seconds"] = round(time.monotonic()-started, 2)
            (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
