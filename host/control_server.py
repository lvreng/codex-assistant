"""Serialize shared local server startup and verify its protocol before a TUI connects."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import time

from model_control import ModelRPC


def ready(home, path):
    if not path.is_socket():
        return False
    rpc = ModelRPC(home, path)
    try:
        rpc.start()
        return True
    except Exception:
        return False
    finally:
        rpc.close()


def ensure(binary, home, path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path.parent / "pet-launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready(home, path):
            return
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet",
                                 "codex-pet-control.service"], check=False).returncode == 0
        if not active:
            subprocess.run(["systemctl", "--user", "reset-failed", "codex-pet-control.service"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            subprocess.run([
                "systemd-run", "--user", "--unit=codex-pet-control", "--collect",
                f"--property=WorkingDirectory={Path.home()}",
                f"--setenv=CODEX_HOME={home}", "--setenv=CODEX_PET_BACKGROUND_SERVER=1",
                f"--setenv=PATH={os.environ['PATH']}", binary, "app-server", "--listen", f"unix://{path}"],
                check=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if ready(home, path):
                return
            time.sleep(.1)
        raise RuntimeError("Codex local control server is not ready; existing conversations were not stopped")


if __name__ == "__main__":
    try:
        ensure(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ESP32 model control: {exc}", file=sys.stderr)
        sys.exit(1)
