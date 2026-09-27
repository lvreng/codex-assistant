#!/usr/bin/env python3
"""Install user-local Linux launchers; never changes firmware or API settings."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "desktop"))
from renderer import LIBRARY, Renderer
from verify import fixture, image
from protocol import packet


def main():
    if not LIBRARY.exists():
        raise SystemExit("Run bash desktop/build.sh first")
    renderer = Renderer()
    view = fixture("glass")
    renderer.feed(packet(1, **view))
    for _ in range(90):
        renderer.tick(16)
    icon = PROJECT / "desktop/icon.png"
    image(renderer).crop((70, 100, 460, 440)).resize((256, 256), Image.Resampling.LANCZOS).save(icon)
    print("Install [##....] 33% | 1/3 | application icon", flush=True)
    python = PROJECT / ".venv/bin/python"
    entry = PROJECT / "desktop/app.py"
    def quote(path):
        return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
    desktop = (
        "[Desktop Entry]\n"
        "Version=1.0\nType=Application\n"
        "Name=Codex助手\n"
        "Comment=Codex 会话与 MoreCode 用量\n"
        f"Exec={quote(python)} {quote(entry)}\n"
        f"Path={PROJECT}\nIcon={icon}\n"
        "Terminal=false\nCategories=Utility;Development;\n"
        "StartupNotify=true\nStartupWMClass=codex-assistant\n"
    )
    data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    applications = data / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    launcher = applications / "codex-assistant.desktop"
    launcher.write_text(desktop)
    launcher.chmod(0o755)
    result = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, check=True)
    target_dir = Path(result.stdout.strip())
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "Codex助手.desktop"
    shutil.copy2(launcher, target)
    print("Install [####..] 67% | 2/3 | application menu and desktop shortcut", flush=True)
    subprocess.run(["gio", "set", str(target), "metadata::trusted", "true"],
                   check=False, capture_output=True)
    subprocess.run(["update-desktop-database", str(applications)], check=False)
    print(f"Install [######] 100% | 3/3 | {target}", flush=True)


if __name__ == "__main__":
    main()
