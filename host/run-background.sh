#!/usr/bin/env bash
set -eu
PET_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$PET_PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=/usr/bin/python3
fi
case "${1:-start}" in
  start)
    if systemctl --user is-active --quiet codex-pet.service; then
      echo "Codex 表情状态助手已经在后台运行。"
      exit 0
    fi
    systemctl --user reset-failed codex-pet.service 2>/dev/null || true
    exec systemd-run --user --unit=codex-pet --collect \
      --description="Codex local status to ESP32 expressions" \
      --property=Restart=on-failure --property=RestartSec=3 \
      --working-directory="$PET_PROJECT_DIR" \
      "$PYTHON" -u "$PET_PROJECT_DIR/host/bridge.py"
    ;;
  stop) exec systemctl --user stop codex-pet.service ;;
  status) exec systemctl --user status --no-pager codex-pet.service ;;
  logs) exec journalctl --user -u codex-pet.service -n 30 -f ;;
  *) echo "Usage: bash host/run-background.sh [start|stop|status|logs]" >&2; exit 2 ;;
esac
