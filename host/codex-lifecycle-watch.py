#!/usr/bin/env python3
"""Keep the ESP32 bridge aligned with the local Codex CLI lifecycle."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time
from desktop_presence import desktop_active


PROJECT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT / "host" / "run-background.sh"
POLL_SECONDS = 0.25


def is_codex_process(pid: int) -> bool:
    if pid == os.getpid():
        return False
    try:
        command = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        executable = os.path.basename(
            os.readlink(Path("/proc") / str(pid) / "exe")
        )
    except (OSError, ValueError):
        return False
    args = [part.decode("utf-8", "replace") for part in command.split(b"\0") if part]
    if not args:
        return False
    if executable == "codex" or any("@openai/codex" in arg for arg in args):
        try:
            environment = (Path("/proc") / str(pid) / "environ").read_bytes().split(b"\0")
            if (b"CODEX_PET_ACCOUNT_READER=1" in environment or
                    b"CODEX_PET_BACKGROUND_SERVER=1" in environment):
                return False
        except OSError:
            pass
    if executable == "codex":
        return True
    return any("@openai/codex" in arg for arg in args)


def codex_count() -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit() and is_codex_process(int(entry.name)):
            count += 1
    return count


def bridge_active() -> bool:
    return subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "codex-pet.service"],
        check=False,
    ).returncode == 0


def bridge_start() -> None:
    subprocess.run(["/usr/bin/bash", str(START_SCRIPT), "start"], check=False)


def bridge_stop() -> None:
    subprocess.run(
        ["systemctl", "--user", "stop", "codex-pet.service"],
        check=False,
    )


def main() -> int:
    stopped = False

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    previous = None
    while not stopped:
        count = codex_count()
        if count != previous:
            print(f"[CODEX] 检测到 {count} 个 Codex 进程", flush=True)
            previous = count
        needed = count > 0 or desktop_active()
        if needed and not bridge_active():
            bridge_start()
        elif not needed and bridge_active():
            bridge_stop()
        time.sleep(POLL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
