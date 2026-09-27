"""Local TUI presence, separate from app-server's retained writer locks."""
import json
import os
from pathlib import Path
import uuid


def process_identity(pid, proc=Path("/proc")):
    try:
        fields = (proc / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] if fields[0] != "Z" else None
    except (OSError, IndexError, ValueError):
        return None


def interactive_pids(proc=Path("/proc")):
    result = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() != "codex":
                continue
            args = (entry / "cmdline").read_bytes().split(b"\0")
            if b"app-server" in args or b"exec" in args:
                continue
            if process_identity(int(entry.name), proc):
                result.add(int(entry.name))
        except OSError:
            continue
    return result


class Presence:
    """Only metadata goes on disk; all actual RPC traffic remains unchanged."""
    def __init__(self, directory, server_pid):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.pid = os.getpid()
        self.path = self.directory / f"{self.pid}-{uuid.uuid4().hex}.json"
        self.server_pid = server_pid
        self.server_start = process_identity(server_pid)
        self.start = process_identity(self.pid)
        self.pending = {}
        self.known, self.active = set(), set()

    def save(self):
        record = dict(pid=self.pid, start=self.start, server_pid=self.server_pid,
                      server_start=self.server_start, known=sorted(self.known),
                      active=sorted(self.active))
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(record))
        temp.replace(self.path)

    def client(self, message):
        method = message.get("method")
        if "id" in message and method in (
                "thread/start", "thread/resume", "thread/fork", "thread/unsubscribe"):
            self.pending[message["id"]] = (method, (message.get("params") or {}).get("threadId"))

    def server(self, message):
        request = self.pending.pop(message.get("id"), None)
        if not request or "error" in message:
            return
        method, requested_id = request
        result = message.get("result") or {}
        if method == "thread/unsubscribe":
            self.active.discard(requested_id)
        else:
            thread_id = (result.get("thread") or {}).get("id")
            if isinstance(thread_id, str):
                self.known.add(thread_id)
                self.active.add(thread_id)
        self.save()

    def close(self):
        self.active.clear()
        self.save()


def visible_locks(locks, codex_dir, proc=Path("/proc")):
    known, active = set(), set()
    for path in (Path(codex_dir) / "pet-client-presence").glob("*.json"):
        try:
            record = json.loads(path.read_text())
            server_pid = record["server_pid"]
            if not record["server_start"] or process_identity(server_pid, proc) != record["server_start"]:
                continue
            owned = {tid for tid, pid in locks.items() if pid == server_pid}
            known.update(owned.intersection(record["known"]))
            if record["start"] and process_identity(record["pid"], proc) == record["start"]:
                active.update(owned.intersection(record["active"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    result = {tid: pid for tid, pid in locks.items() if tid not in known or tid in active}
    # Also handle older direct clients when the very last TUI has exited.
    if result and not interactive_pids(proc):
        for tid, pid in list(result.items()):
            try:
                env = (proc / str(pid) / "environ").read_bytes().split(b"\0")
                if b"CODEX_PET_BACKGROUND_SERVER=1" in env:
                    del result[tid]
            except OSError:
                pass
    return result
