"""Read-only, version-aware Codex CLI status adapter; never evaluates log content.

Uses turn metadata plus incremental semantic events, not reasoning text, token
counts, process CPU, or mere file freshness. The adapter is for local CLI sessions;
the app-server's private runtime is not imitated by launching a second server.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import re
import sqlite3
import time
from client_presence import visible_locks

STATES = ("idle", "thinking", "working", "waiting", "done", "error", "paused", "offline", "searching")
LABELS = dict(zip(STATES, ("待机", "思考中", "制作中", "待确认", "已完成", "出错了", "已暂停", "未连接", "查资料")))
ACTIVE_STATES = {"thinking", "working", "searching"}
ROTATION_MODES = ("off", "active", "all")
TOOLS_WAIT = {"request_user_input", "request_user_input_async", "request_permissions"}
TOOLS_SEARCH = {"web.run", "web__run", "web_search", "webSearch"}
TOOL_STATE_HOLD_SECONDS = 0.8


def _norm(value) -> str:
    return str(value or "").strip().replace("-", "_").lower()


def _model_value(value) -> str:
    return value.strip()[:160] if isinstance(value, str) else ""


def _tool_state(name: str) -> str:
    normalized = _norm(name)
    short = normalized.rsplit(".", 1)[-1]
    wait_names = {_norm(value) for value in TOOLS_WAIT}
    search_names = {_norm(value) for value in TOOLS_SEARCH}
    if normalized in wait_names or short in wait_names:
        return "waiting"
    if normalized in search_names or short in {"web_search", "websearch", "web__run"}:
        return "searching"
    return "working"


@dataclass
class Session:
    id: str
    title: str
    short: str
    state: str
    turn_id: str = ""
    updated: float = 0
    children: int = 0
    source: str = "local-cli"
    usage_tokens: int = 0xFFFFFFFF
    duration_seconds: int = 0
    model: str = ""
    model_provider: str = ""
    input_tokens: int = 0xFFFFFFFFFFFFFFFF
    output_tokens: int = 0xFFFFFFFFFFFFFFFF

    def public(self):
        return asdict(self)


def short_name(title: str, cwd: str, thread_id: str, aliases: dict) -> str:
    value = aliases.get(thread_id) or aliases.get(cwd) or title or Path(cwd).name or thread_id[:8]
    value = re.sub(r"[\x00-\x1f\x7f]", "", str(value)).strip()
    value = re.sub(r"^(请帮我|帮我|请|现在)(?=.)", "", value)
    # Explicit names win; trim only the on-device display, never rename Codex.
    return value[:12] if value else thread_id[:8]


def locked_threads(codex_dir: Path, proc_dir: Path = Path("/proc")) -> dict[str, int]:
    """Inspect existing kernel locks; do not acquire, release or create any lock."""
    identities = {}
    for path in (codex_dir / "thread-writer-locks").glob("*.lock"):
        if path.name.startswith("."):
            continue
        try:
            st = path.stat()
            identities[(os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)] = path.stem
        except OSError:
            pass
    result = {}
    for line in (proc_dir / "locks").read_text().splitlines():
        fields = line.split()
        if len(fields) < 6 or fields[1] == "->" or fields[1] != "FLOCK":
            continue
        try:
            major, minor, inode = fields[5].split(":")
            key = (int(major, 16), int(minor, 16), int(inode))
            pid = int(fields[4])
            if key in identities and (proc_dir / str(pid) / "comm").read_text().strip() == "codex":
                result[identities[key]] = pid
        except (OSError, ValueError):
            pass
    return visible_locks(result, codex_dir, proc_dir)


class EventState:
    def __init__(self, turn_id="", model=""):
        self.turn_id = turn_id
        self.model = _model_value(model)
        self.fallback_model = self.model
        self.phase = "thinking"
        self.pending = {}
        self.async_question = False
        self.last_event = 0.0
        self.observed = False
        self.usage_tokens = 0xFFFFFFFF
        self.input_tokens = self.output_tokens = 0xFFFFFFFFFFFFFFFF
        self.model_provider = ""
        self.last_tool_state = ""
        self.last_tool_at = 0.0

    def reset(self, turn_id, model=None):
        counts = self.input_tokens, self.output_tokens, self.model_provider
        self.__init__(turn_id, self.fallback_model)
        self.input_tokens, self.output_tokens, self.model_provider = counts
        if model is not None:
            self.model = _model_value(model)

    def set_fallback_model(self, model):
        model = _model_value(model)
        if self.model == self.fallback_model or not self.model or (
                model and model != self.fallback_model):
            self.model = model
        self.fallback_model = model

    def feed(self, event):
        if not isinstance(event, dict):
            return
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            return
        event_type = _norm(event.get("type"))
        kind = _norm(payload.get("type"))
        if event_type == "turn_context" and isinstance(payload.get("model"), str):
            self.model = _model_value(payload["model"])
        if event_type in ("turn_context", "session_meta") and isinstance(payload.get("model_provider"), str):
            self.model_provider = payload["model_provider"][:80]
        self.last_event = time.monotonic()
        if event_type == "event_msg" and kind == "token_count":
            try:
                value = int(payload["info"]["total_token_usage"]["total_tokens"])
                self.usage_tokens = max(0, min(0xFFFFFFFE, value))
                totals = payload["info"]["total_token_usage"]
                self.input_tokens = max(0, min(0xFFFFFFFFFFFFFFFE, int(totals["input_tokens"])))
                self.output_tokens = max(0, min(0xFFFFFFFFFFFFFFFE, int(totals["output_tokens"])))
            except (KeyError, TypeError, ValueError):
                pass
            return
        if kind in (
            "task_started", "turn_started", "task_complete", "task_completed",
            "task_failed", "turn_failed", "turn_aborted", "turn_interrupted",
            "item_started", "item_completed",
            "function_call", "custom_tool_call", "function_call_output",
            "custom_tool_call_output", "reasoning",
        ):
            self.observed = True
        if kind in ("task_started", "turn_started"):
            model = self.model
            self.reset(payload.get("turn_id", self.turn_id), model)
            self.observed = True
            return
        if kind in ("user_message", "user_msg") or (
                event.get("type") == "response_item" and kind == "message" and payload.get("role") == "user"):
            # CLI 0.154 records steered async answers as user response items,
            # without an accompanying event_msg/user_message entry.
            self.async_question = False
            return
        if kind in ("task_complete", "task_completed"):
            self.pending.clear()
            self.phase = "done"
            return
        if kind in ("task_failed", "turn_failed"):
            self.pending.clear()
            self.async_question = False
            self.phase = "error"
            return
        if kind in ("turn_aborted", "turn_interrupted"):
            self.pending.clear()
            self.async_question = False
            self.phase = "paused"
            return
        if event_type == "event_msg" and kind in ("item_started", "item_completed"):
            item = payload.get("item", {})
            if not isinstance(item, dict):
                return
            typ = _norm(item.get("type"))
            if typ == "agentmessage" and item.get("questions"):
                self.async_question = True
            if kind == "item_started" and typ in ("commandexecution", "filechange", "mcptoolcall"):
                self.phase = "working"
            elif typ == "reasoning":
                self.phase = "thinking"
            return
        if event_type != "response_item":
            return
        if kind in ("function_call", "custom_tool_call"):
            name = str(payload.get("name", ""))
            call_id = payload.get("call_id", name)
            state = _tool_state(name)
            if _norm(name).rsplit(".", 1)[-1] == "request_user_input_async":
                self.async_question = True
            self.pending[call_id] = state
            self.phase = state
        elif kind in ("function_call_output", "custom_tool_call_output"):
            call_id = payload.get("call_id")
            previous = self.pending.pop(call_id, None)
            if previous:
                self.last_tool_state = previous
                self.last_tool_at = time.monotonic()
            self.phase = "thinking"
        elif kind == "reasoning":
            # Only the item TYPE is used. summary/content/encrypted_content are ignored.
            self.phase = "thinking"

    def state(self):
        if self.async_question or "waiting" in self.pending.values():
            return "waiting"
        if self.pending:
            return "searching" if all(s == "searching" for s in self.pending.values()) else "working"
        if self.last_tool_state and time.monotonic() - self.last_tool_at < TOOL_STATE_HOLD_SECONDS:
            return self.last_tool_state
        return self.phase


class TailReader:
    INITIAL_BYTES = 262144
    BUDGET = 524288
    MAX_LINE = 1048576

    def __init__(self, path: Path, turn_id: str, model: str = ""):
        self.path, self.events = path, EventState(turn_id, model)
        self.offset = None
        self.inode = None
        self.buffer = b""
        self.discarding = False

    def poll(self):
        st = self.path.stat()
        reset = self.inode != st.st_ino or (self.offset is not None and st.st_size < self.offset)
        with self.path.open("rb") as stream:
            if self.offset is None or reset:
                self.offset = max(0, st.st_size - self.INITIAL_BYTES)
                self.buffer = b""
                self.events.reset(self.events.turn_id)
                self.discarding = self.offset > 0
            self.inode = st.st_ino
            stream.seek(self.offset)
            data = stream.read(self.BUDGET)
            self.offset += len(data)
        if self.discarding:
            first = data.find(b"\n")
            if first < 0:
                return
            data = data[first + 1:]
            self.discarding = False
        pieces = (self.buffer + data).split(b"\n")
        self.buffer = pieces.pop()
        for line in pieces:
            if len(line) > self.MAX_LINE:
                continue
            try:
                self.events.feed(json.loads(line))
            except (ValueError, UnicodeError, TypeError):
                continue
        if len(self.buffer) > self.MAX_LINE:
            self.buffer = b""
            self.discarding = True


def classify(turn: dict | None, events: EventState, items: list[dict]) -> str:
    if not turn:
        return events.state() if events.observed else "idle"
    status = _norm(turn.get("status"))
    if status in ("failed", "error"):
        return "error"
    if status in ("interrupted", "cancelled", "canceled", "aborted"):
        return "paused"
    if status in (
        "waiting", "requiresaction", "requires_action", "waitingonapproval",
        "waiting_on_approval", "waitingonuserinput", "waiting_on_user_input",
    ):
        return "waiting"
    same_turn = not events.turn_id or not turn.get("turn_id") or events.turn_id == turn.get("turn_id")
    if same_turn and events.async_question:
        return "waiting"
    if status == "completed":
        return "done"
    if status not in ("inprogress", "in_progress", "queued"):
        return "idle"
    # Tool failures are not turn failures: the agent may be handling/retrying them.
    for item in items:
        typ = _norm(item.get("type"))
        item_status = _norm(item.get("status"))
        if typ == "agentmessage" and item.get("questions"):
            return "waiting"
        if item_status in (
            "waiting", "waitingonapproval", "waiting_on_approval",
            "waitingonuserinput", "waiting_on_user_input", "requiresaction",
            "requires_action",
        ):
            return "waiting"
    if same_turn and events.observed:
        state = events.state()
        return state
    if any(
        _norm(x.get("status")) in ("inprogress", "in_progress")
        and _norm(x.get("type")) in ("commandexecution", "filechange", "mcptoolcall")
        for x in items
    ):
        return "working"
    return "thinking"


class Detector:
    def __init__(self, codex_dir: Path, aliases=None, lock_provider=None):
        self.codex_dir = codex_dir
        self.aliases = aliases or {}
        self.lock_provider = lock_provider or locked_threads
        self.tails = {}
        self.displayed = {}
        self.error = None

    def database(self, prefix):
        candidates = list(self.codex_dir.glob(prefix + "_*.sqlite"))
        if not candidates:
            raise FileNotFoundError(f"Missing {prefix} database")
        path = max(candidates, key=lambda p: int(p.stem.rsplit("_", 1)[-1]))
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.3)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        state_db = history_db = None
        try:
            active = self.lock_provider(self.codex_dir)
            if not active:
                self.error = None
                self.tails.clear()
                return []
            state_db, history_db = self.database("state"), self.database("thread_history")
            marks = ",".join("?" for _ in active)
            thread_columns = {row[1] for row in state_db.execute("PRAGMA table_info(threads)")}
            columns = "id,rollout_path,name,title,cwd,source,agent_path,updated_at"
            if "model" in thread_columns:
                columns += ",model"
            if "model_provider" in thread_columns:
                columns += ",model_provider"
            rows = [dict(row) for row in state_db.execute(f"SELECT {columns} FROM threads WHERE archived=0 AND id IN ({marks})", tuple(active))]
            children = {}
            roots = []
            for row in rows:
                source = row.get("source", "")
                try:
                    sub = json.loads(source).get("subagent", {}).get("thread_spawn", {})
                except (ValueError, AttributeError):
                    sub = {}
                if sub.get("parent_thread_id"):
                    parent = sub["parent_thread_id"]
                    children[parent] = children.get(parent, 0) + 1
                elif not row.get("agent_path"):
                    roots.append(row)
            result = []
            for row in roots:
                tid = row["id"]
                turn_row = history_db.execute("SELECT turn_id,status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (tid,)).fetchone()
                turn = dict(turn_row) if turn_row else None
                turn_id = turn["turn_id"] if turn else ""
                tail = self.tails.get(tid)
                model = row.get("model") if isinstance(row.get("model"), str) else ""
                if tail is None or (turn_id and tail.events.turn_id != turn_id):
                    tail = self.tails[tid] = TailReader(Path(row["rollout_path"]), turn_id, model)
                else:
                    tail.events.set_fallback_model(model)
                try:
                    tail.poll()
                except OSError:
                    pass  # Database still gives authoritative turn completion/error state.
                items = []
                if turn:
                    for item_row in history_db.execute("SELECT item_json FROM thread_items WHERE thread_id=? AND turn_id=? ORDER BY rollout_ordinal DESC LIMIT 12", (tid, turn_id)):
                        item = json.loads(item_row[0])
                        # Only retain fields needed by the state machine.
                        items.append({k: item[k] for k in ("type", "status", "questions") if k in item})
                state = classify(turn, tail.events, items)
                old_state, changed = self.displayed.get(tid, (state, now))
                if state != old_state:
                    changed = now
                self.displayed[tid] = (state, changed)
                title = row.get("name") or row.get("title") or Path(row["cwd"]).name
                started_at = float(turn.get("started_at") or 0) if turn else 0.0
                completed_at = float(turn.get("completed_at") or 0) if turn else 0.0
                end_at = completed_at if completed_at > started_at else time.time()
                duration = max(0, int(end_at - started_at)) if started_at else 0
                result.append(Session(tid, title, short_name(title, row["cwd"], tid, self.aliases), state, turn_id,
                                      float(row.get("updated_at", 0)), children.get(tid, 0),
                                      usage_tokens=tail.events.usage_tokens,
                                      duration_seconds=duration,
                                      model=tail.events.model,
                                      model_provider=tail.events.model_provider or row.get("model_provider", ""),
                                      input_tokens=tail.events.input_tokens,
                                      output_tokens=tail.events.output_tokens))
            self.tails = {k: v for k, v in self.tails.items() if k in active}
            self.error = None
            return sorted(result, key=lambda x: (-x.updated, x.id))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return []
        finally:
            if state_db is not None:
                state_db.close()
            if history_db is not None:
                history_db.close()


class Chooser:
    def __init__(self, interval=5.0, manual_timeout=3.0, mode="active"):
        if interval <= 0 or mode not in ROTATION_MODES:
            raise ValueError("Invalid conversation rotation settings")
        self.interval = interval
        self.manual_timeout = manual_timeout
        self.mode = mode
        self.current = None
        self.since = 0.0
        self.manual_until = None
        self.waiting = set()

    def configure(self, mode, now, interval=None):
        interval = self.interval if interval is None else interval
        if interval <= 0 or mode not in ROTATION_MODES:
            raise ValueError("Invalid conversation rotation settings")
        changed = mode != self.mode or interval != self.interval
        self.mode, self.interval = mode, interval
        if changed:
            self.since = now
        return changed

    @staticmethod
    def ordered(sessions: list[Session]) -> list[Session]:
        priorities = {"waiting": 0, "error": 1, "working": 2, "searching": 2, "thinking": 2}
        return sorted(sessions, key=lambda s: (priorities.get(s.state, 3), s.id))

    def choose(self, sessions: list[Session], now: float):
        if not sessions:
            self.current = None
            self.manual_until = None
            self.waiting.clear()
            return None
        ordered = self.ordered(sessions)
        waiting = {s.id for s in ordered if s.state == "waiting"}
        all_ids = [s.id for s in ordered]
        if self.manual_until is not None and self.current in all_ids and now < self.manual_until:
            self.waiting = waiting
            return next(s for s in ordered if s.id == self.current)
        self.manual_until = None

        active = [s for s in ordered if s.state in ACTIVE_STATES]
        if self.mode == "active" and active:
            candidates, timed = active, True
        else:
            candidates, timed = ordered, self.mode == "all"
        ids = [s.id for s in candidates]
        self.waiting = waiting
        if self.current not in ids:
            self.current, self.since = ids[0], now
        elif len(ids) == 1:
            self.since = now
        elif timed and now - self.since >= self.interval:
            self.current, self.since = ids[(ids.index(self.current) + 1) % len(ids)], now
        return next(s for s in candidates if s.id == self.current)

    def step(self, sessions: list[Session], delta: int, now: float):
        ordered = self.ordered(sessions)
        if not ordered:
            self.current = None
            self.since = now
            return None
        ids = [session.id for session in ordered]
        if self.current not in ids:
            index = 0
        else:
            index = (ids.index(self.current) + delta) % len(ids)
        self.current = ids[index]
        self.since = now
        self.manual_until = now + self.manual_timeout
        self.waiting = {session.id for session in ordered if session.state == "waiting"}
        return ordered[index]
