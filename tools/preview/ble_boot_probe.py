"""Capture C6/BLE startup without toggling the P4 USB reset lines."""
import argparse
from pathlib import Path
import subprocess
import sys
import time

import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("port")
parser.add_argument("--flash", type=Path)
parser.add_argument("--esptool-python", default=sys.executable)
parser.add_argument("--seconds", type=float, default=25)
parser.add_argument("--log", type=Path, required=True)
args = parser.parse_args()
if args.flash:
    subprocess.run([args.esptool_python, "-m", "esptool", "--chip", "esp32p4",
                    "--port", args.port, "--baud", "460800", "--before", "default-reset",
                    "--after", "watchdog-reset", "write-flash", "0x10000", str(args.flash)], check=True)
port = serial.Serial(port=None, baudrate=115200, timeout=.1, exclusive=True)
port.dtr = port.rts = True
port.port = args.port
port.open()
started = time.monotonic()
pending = b""
args.log.parent.mkdir(parents=True, exist_ok=True)
try:
    with args.log.open("wb") as log:
        while time.monotonic() - started < args.seconds:
            data = port.read(max(1, port.in_waiting))
            log.write(data)
            log.flush()
            pending += data
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                text = line.decode("utf-8", "replace").strip()
                if not text.startswith("@") or text.startswith(("@BLE", "@ART_STAT", "@STAT")):
                    print(text, flush=True)
finally:
    port.close()
