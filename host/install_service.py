#!/usr/bin/env python3
"""Install the lifecycle watcher for the current checkout and Linux user."""
import argparse
from datetime import datetime
import os
from pathlib import Path
import shutil
import subprocess

PROJECT = Path(__file__).resolve().parents[1]
UNIT = "codex-pet-lifecycle.service"


def quote(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("Newlines are not supported in service paths")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def unit_text(project, path):
    return (
        "[Unit]\nDescription=Codex assistant lifecycle watcher\n"
        "After=graphical-session.target\nPartOf=graphical-session.target\n\n"
        "[Service]\nType=simple\n"
        f"Environment={quote('PATH=' + path)}\n"
        f"ExecStart={quote(project / '.venv/bin/python')} -u "
        f"{quote(project / 'host/codex-lifecycle-watch.py')}\n"
        "Restart=always\nRestartSec=2\n\n[Install]\nWantedBy=default.target\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print only; do not install or start")
    parser.add_argument("--enable", action="store_true", help="Enable and restart the watcher after installation")
    args = parser.parse_args()
    if not (PROJECT / ".venv/bin/python").is_file():
        raise SystemExit("Create the project .venv and install requirements first")
    unit = unit_text(PROJECT, os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
    if args.dry_run:
        print(unit, end="")
        return
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    target = config / "systemd/user" / UNIT
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(".service.backup-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(target, backup)
        print(f"Previous unit backed up: {backup}")
    temporary = target.with_suffix(".service.tmp")
    temporary.write_text(unit)
    temporary.replace(target)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if args.enable:
        subprocess.run(["systemctl", "--user", "enable", UNIT], check=True)
        subprocess.run(["systemctl", "--user", "restart", UNIT], check=True)
    print(f"Installed: {target}")


if __name__ == "__main__":
    main()
