#!/usr/bin/env python3
"""Verify a disposable, prompt-free Codex thread on an isolated local socket."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from model_control import ModelRPC, APPLIED, MODELS


def main():
    with tempfile.TemporaryDirectory(prefix="pet-model-smoke-") as temp:
        sock = Path(temp) / "server.sock"
        process = subprocess.Popen(
            ["codex", "app-server", "--listen", f"unix://{sock}"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            env={**os.environ, "CODEX_PET_ACCOUNT_READER": "1"})
        rpc = ModelRPC(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")), sock)
        observer = ModelRPC(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")), sock)
        try:
            deadline = time.monotonic() + 20
            while not sock.is_socket():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("temporary app-server failed to start")
                time.sleep(.1)
            print("[1/6] Isolated socket ready", flush=True)
            time.sleep(.5)
            if process.poll() is not None:
                raise RuntimeError(f"temporary server exited: {process.returncode}")
            rpc.start()
            models = rpc.call("model/list", {"limit": 100}).get("data", [])
            names = {row.get("model") for row in models}
            choices = [name for name in MODELS if name in names]
            if len(choices) < 2:
                raise RuntimeError("fewer than two requested models advertised")
            thread = rpc.call("thread/start", {"cwd": str(ROOT), "ephemeral": True,
                                                "model": choices[0]})["thread"]
            print("[2/6] Disposable thread created; no prompt submitted", flush=True)
            result = observer.inspect(thread["id"], MODELS.index(choices[-1]) + 1)
            if result[0] != APPLIED:
                raise RuntimeError(f"model update not verified: status={result[0]}")
            print("[3/6] Device-to-Codex model update verified", flush=True)
            final = rpc.call("thread/read", {"threadId": thread["id"], "includeTurns": False})["thread"]
            assert not final["turns"], "must not start a model turn"
            started = time.monotonic()
            rpc.call("thread/settings/update", {"threadId": thread["id"], "model": choices[0]})
            assert observer.observe([thread["id"]]) == {thread["id"]: choices[0]}
            reverse_ms = round((time.monotonic() - started) * 1000, 1)
            print(f"[4/6] Codex-to-device readback verified ({reverse_ms} ms)", flush=True)
            from client_presence import Presence, visible_locks
            presence = Presence(Path(temp) / "pet-client-presence", process.pid)
            presence.client({"id": 1, "method": "thread/start", "params": {}})
            presence.server({"id": 1, "result": {"thread": {"id": thread["id"]}}})
            presence.close()
            assert visible_locks({thread["id"]: process.pid}, Path(temp)) == {}
            assert thread["id"] in observer.call("thread/loaded/list", {})["data"]
            print("[5/6] Closed client hidden while server thread remains loaded", flush=True)
            print(json.dumps({"passed": True, "from": choices[0], "to": final["model"],
                              "turns": len(final["turns"]), "prompts_sent": 0,
                              "reverse_sync_ms": reverse_ms}), flush=True)
            print("[6/6] Complete", flush=True)
        finally:
            observer.close()
            rpc.close()
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdin.close()


if __name__ == "__main__":
    main()
