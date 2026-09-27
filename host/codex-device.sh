#!/usr/bin/env bash
set -euo pipefail
# A shared local server allows the ESP32 to update a loaded thread's settings.
# It does not resume or take over any existing standalone CLI process.
binary="${CODEX_PET_REAL_CODEX:-$HOME/.npm-global/bin/codex}"
[[ -x "$binary" ]] || { printf 'Original Codex executable was not found: %s\n' "$binary" >&2; exit 1; }
project="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
codex_home="${CODEX_HOME:-$HOME/.codex}"
socket="$codex_home/app-server-control/app-server-control.sock"
umask 077
"$project/.venv/bin/python" "$project/host/control_server.py" "$binary" "$codex_home" "$socket"
exec "$project/.venv/bin/python" "$project/host/codex_client.py" "$binary" "$socket" "$@"
