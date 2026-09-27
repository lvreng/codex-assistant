"""Per-thread model selection via an existing Codex app-server, never a new resume.

The bridge remains metadata-only. No prompts, inference, global config writes,
rollout edits or terminal keystrokes are used to switch models.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import json
import secrets
import time

from websockets.sync.client import unix_connect
from websockets.exceptions import WebSocketException

MODELS = ("gpt-6-astra", "gpt-6-sol", "gpt-6-luna",
          "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
CONTEXT, LOADING, READY, QUEUED, APPLIED, UNAVAILABLE, FAILED, GONE = range(8)
CATALOG_TTL = 300
RPC_TIMEOUTS = {"model/list": 20, "thread/settings/update": 20}


class RPCRejected(RuntimeError):
    def __init__(self, method, code):
        # Do not log an arbitrary upstream error body, which may contain credentials.
        super().__init__(f"{method} rejected (code={code})")


def model_key(name):
    return MODELS.index(name) + 1 if name in MODELS else 0


class ModelRPC:
    def __init__(self, codex_dir, socket_path=None):
        self.socket_path = Path(socket_path or Path(codex_dir) /
                                "app-server-control/app-server-control.sock")
        self.connection = None
        self.sequence = 0
        self.catalog = None
        self.catalog_at = -1000.0
        self.catalog_identity = None
        self.last_method = "connect"
        self.last_error = ""
        self.last_error_at = -1000.0

    def start(self):
        self.last_method = "connect"
        self.connection = unix_connect(str(self.socket_path), open_timeout=3,
                                       close_timeout=1, max_size=2 ** 22,
                                       compression=None)
        self.call("initialize", {"clientInfo": {"name": "codex_pet_model_picker",
                  "version": "1.0"}, "capabilities": {"experimentalApi": True}})
        self.connection.send(json.dumps({"method": "initialized", "params": {}}))

    def call(self, method, params):
        self.last_method = method
        self.sequence += 1
        self.connection.send(json.dumps({"id": self.sequence, "method": method, "params": params}))
        timeout = RPC_TIMEOUTS.get(method, 5)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = json.loads(self.connection.recv(timeout=max(.01, deadline - time.monotonic())))
            except TimeoutError as exc:
                raise TimeoutError(f"{method} timed out after {timeout}s") from exc
            if "method" in message:
                if "id" in message:
                    self.connection.send(json.dumps({"id": message["id"], "error": {
                        "code": -32601, "message": "Model picker does not handle tool requests"}}))
                continue
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RPCRejected(method, message["error"].get("code"))
                return message.get("result") or {}
        raise TimeoutError(f"{method} timed out after {timeout}s")

    def available_mask(self):
        info = self.socket_path.stat()
        identity = (info.st_dev, info.st_ino, info.st_mtime_ns)
        now = time.monotonic()
        if (self.catalog is not None and identity == self.catalog_identity
                and now - self.catalog_at < CATALOG_TTL):
            return self.catalog
        available, cursor = set(), None
        for _ in range(20):
            result = self.call("model/list", {"limit": 100, "cursor": cursor})
            available.update(row.get("model") for row in result.get("data", []))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise RuntimeError("model/list exceeded page limit")
        # Cache only a complete server response, never guessed or unrelated account models.
        self.catalog = sum(1 << i for i, model in enumerate(MODELS) if model in available)
        self.catalog_identity = identity
        self.catalog_at = time.monotonic()
        return self.catalog

    def failed(self, operation, exc):
        detail = str(exc) if isinstance(exc, (TimeoutError, RPCRejected)) else type(exc).__name__
        error = f"{operation} stage={self.last_method}: {detail}"
        now = time.monotonic()
        if error != self.last_error or now - self.last_error_at >= 30:
            print(f"[MODEL_CONTROL] {error}", flush=True)
            self.last_error_at = now
        self.last_error = error

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def observe(self, thread_ids):
        """Read live settings without subscribing or prolonging a thread's life."""
        if not self.socket_path.is_socket():
            return {}
        try:
            if self.connection is None:
                self.start()
            loaded, cursor = set(), None
            for _ in range(20):
                result = self.call("thread/loaded/list", {"limit": 100, "cursor": cursor})
                loaded.update(result.get("data", []))
                cursor = result.get("nextCursor")
                if not cursor:
                    break
            models = {}
            for thread_id in thread_ids:
                if thread_id not in loaded:
                    continue
                thread = self.call("thread/read", {"threadId": thread_id,
                                                   "includeTurns": False})["thread"]
                model = thread.get("model")
                if thread.get("status", {}).get("type") != "notLoaded" and isinstance(model, str):
                    models[thread_id] = model
            return models
        except (OSError, RuntimeError, TimeoutError, KeyError, TypeError, ValueError, WebSocketException) as exc:
            self.failed("observe", exc)
            self.close()
            return {}

    def inspect(self, thread_id, target=0):
        if not self.socket_path.is_socket():
            return UNAVAILABLE, 0, 0
        try:
            if self.connection is None:
                self.start()
            thread = self.call("thread/read", {"threadId": thread_id,
                                               "includeTurns": False})["thread"]
            state = thread.get("status", {}).get("type")
            current = model_key(thread.get("model"))
            if state == "notLoaded":
                return UNAVAILABLE, 0, current
            mask = self.available_mask()
            if not target:
                return READY, mask, current
            if not 1 <= target <= len(MODELS) or not mask & (1 << (target - 1)):
                return FAILED, mask, current
            # Catalog refresh may take seconds; recheck live state before changing anything.
            thread = self.call("thread/read", {"threadId": thread_id,
                                               "includeTurns": False})["thread"]
            state = thread.get("status", {}).get("type")
            current = model_key(thread.get("model"))
            if state == "active":
                return QUEUED, mask, current
            if state == "notLoaded":
                return UNAVAILABLE, mask, current
            if state != "idle":
                return FAILED, mask, current
            if current == target:
                return APPLIED, mask, current
            # Settings apply to subsequent turns only, even if a turn starts
            # between this status read and the update. Never interrupt a turn.
            self.call("thread/settings/update", {"threadId": thread_id,
                                                   "model": MODELS[target - 1]})
            verified = self.call("thread/read", {"threadId": thread_id,
                                                 "includeTurns": False})["thread"]
            actual = model_key(verified.get("model"))
            if actual == target and verified.get("status", {}).get("type") == "active":
                return QUEUED, mask, actual
            return (APPLIED if actual == target else FAILED), mask, actual
        except (OSError, RuntimeError, TimeoutError, KeyError, TypeError, ValueError, WebSocketException) as exc:
            self.failed("inspect", exc)
            self.close()
            return FAILED, 0, 0


@dataclass
class Selection:
    thread_id: str
    context: int
    request: int
    title: str
    status: int = LOADING
    mask: int = 0
    current: int = 0
    target: int = 0
    checked: float = -100

    def packet_args(self):
        return dict(status=self.status, mask=self.mask, current=self.current,
                    context=self.context, request=self.request, title=self.title)


class ModelControl:
    def __init__(self, codex_dir, rpc=None):
        self.rpc = rpc or ModelRPC(codex_dir)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-control")
        self.future = None
        self.job = None
        self.contexts = {}
        self.selection = None
        self.pending = {}
        self.pinned_until = 0.0
        self.current_id = None
        self.current_context = 0
        self.current_title = ""
        self.current_model = 0
        self.applied = {}
        self.observed = {}
        self.last_observation = -100.0
        self.last_result = None

    def context(self, session, now):
        thread_id = session.id if session else None
        if thread_id != self.current_id:
            self.current_context = secrets.randbelow(0xFFFFFFFE) + 1
            self.current_id = thread_id
        self.current_title = session.short if session else "暂无对话"
        self.current_model = model_key(session.model) if session else 0
        if session:
            self.contexts[self.current_context] = (session.id, session.short, now)
        self.contexts = {key: value for key, value in self.contexts.items()
                         if now - value[2] < 60}
        if self.selection and now < self.pinned_until:
            return self.selection.packet_args()
        return dict(status=CONTEXT, mask=0, current=self.current_model,
                    context=self.current_context, request=0, title=self.current_title)

    def pinned(self, now):
        return self.selection.thread_id if self.selection and now < self.pinned_until else None

    def handle(self, action, context, request, key, now):
        if action in ("pin", "open"):
            if self.selection and (self.selection.context, self.selection.request) == (context, request):
                self.pinned_until = now + 20
                return
            entry = self.contexts.get(context)
            if not entry or now - entry[2] >= 60:
                self.selection = Selection("", context, request, "暂无对话", GONE)
            else:
                self.selection = Selection(entry[0], context, request, entry[1])
            print(f"[MODEL_CONTROL] {action} thread={self.selection.thread_id} request={request}", flush=True)
            self.pinned_until = now + 20
        elif self.selection and (self.selection.context, self.selection.request) == (context, request):
            selected = self.selection
            if action in ("cancel", "close"):
                self.pinned_until = 0
                if action == "cancel" and not selected.target:
                    self.selection = None
            elif (action == "confirm" and selected.status == READY and
                  1 <= key <= len(MODELS) and selected.mask & (1 << (key - 1))):
                selected.target = key
                selected.status = QUEUED
                selected.checked = -100
                self.pending[selected.thread_id] = selected
                self.pinned_until = now + 20

    def poll(self, sessions, now):
        if self.future and self.future.done():
            result = self.future.result()
            job, self.job, self.future = self.job, None, None
            if job is None:
                self.observed = result
                self.last_observation = now
                for thread_id in result:
                    self.applied.pop(thread_id, None)
                    pending = self.pending.get(thread_id)
                    if pending and model_key(result[thread_id]) not in (pending.current, pending.target):
                        # A newer /model choice supersedes a device choice still queued.
                        pending.status = READY
                        pending.target = 0
                        self.pending.pop(thread_id, None)
                if self.selection and self.selection.thread_id in result:
                    self.selection.current = model_key(result[self.selection.thread_id])
            else:
                status, mask, current = result
                signature = (job.thread_id, job.request, status, mask, current, job.target)
                if signature != self.last_result:
                    print(f"[MODEL_CONTROL] result thread={job.thread_id} request={job.request} "
                          f"status={status} mask={mask} current={current} target={job.target}", flush=True)
                    self.last_result = signature
                job.status, job.mask, job.current = status, mask, current
                job.checked = now
                if status == APPLIED:
                    turn = next((s.turn_id for s in sessions if s.id == job.thread_id), None)
                    self.applied[job.thread_id] = (MODELS[current - 1], turn)
                    self.observed.pop(job.thread_id, None)
                if job.target and status != QUEUED:
                    if self.pending.get(job.thread_id) is job:
                        self.pending.pop(job.thread_id, None)
        if self.future:
            return
        ids = {session.id for session in sessions}
        self.applied = {k: v for k, v in self.applied.items() if k in ids}
        self.observed = {k: v for k, v in self.observed.items() if k in ids}
        if self.selection and self.selection.thread_id not in ids:
            self.selection.status = GONE
            self.pinned_until = 0
        # A closed target is never silently replaced with the newly selected chat.
        for thread_id, pending in list(self.pending.items()):
            if thread_id not in ids:
                pending.status = GONE
                self.pending.pop(thread_id)
        jobs = list(self.pending.values())
        if self.selection and self.selection.status == LOADING and not self.selection.target:
            jobs.insert(0, self.selection)
        for job in jobs:
            if now - job.checked < 2:
                continue
            if not job.thread_id or job.thread_id not in ids:
                job.status = GONE
                continue
            self.job = job
            self.future = self.pool.submit(self.rpc.inspect, job.thread_id, job.target)
            return
        if ids and now - self.last_observation >= .25:
            self.job = None
            self.future = self.pool.submit(self.rpc.observe, tuple(ids))

    def sync_models(self, sessions):
        for session in sessions:
            if session.id in self.observed:
                session.model = self.observed[session.id]
            if session.id in self.applied:
                model, turn = self.applied[session.id]
                if session.turn_id == turn:
                    session.model = model
                else:
                    del self.applied[session.id]

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.rpc.close()
