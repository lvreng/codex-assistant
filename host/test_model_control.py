import binascii
from dataclasses import dataclass
import json
import struct
import time
from types import SimpleNamespace
import pytest

from model_control import (APPLIED, FAILED, GONE, MODELS, ModelControl, ModelRPC,
                           QUEUED, READY, UNAVAILABLE, CATALOG_TTL, RPCRejected)
import model_control
from protocol import MAGIC, model_picker_packet
from codex_state import Chooser
from text_labels import mask as text_mask


class FakeConnection:
    def close(self):
        pass


class Fakecall:
    def __init__(self, thread, catalog=MODELS, verified=None):
        self.thread = thread
        self.catalog = list(catalog)
        self.verified = verified
        self.calls = []

    def __call__(self, method, params=None):
        self.calls.append((method, params))
        if method == "thread/read":
            updated = any(call[0] == "thread/settings/update" for call in self.calls)
            thread = self.verified if updated and self.verified is not None else self.thread
            return {"thread": dict(thread)}
        if method == "model/list":
            return {"data": [{"model": model} for model in self.catalog]}
        if method == "thread/settings/update":
            return {}
        raise AssertionError(f"unexpected RPC method: {method}")


def rpc_with_socket(tmp_path, fake):
    path = tmp_path / "control.sock"
    rpc = ModelRPC(tmp_path, path)
    # These are RPC unit tests, not transport tests; no real socket is needed.
    rpc.socket_path = SimpleNamespace(is_socket=lambda: True,
        stat=lambda: SimpleNamespace(st_dev=1, st_ino=2, st_mtime_ns=3))
    rpc.connection = FakeConnection()
    rpc.call = fake
    return rpc, rpc.connection


@pytest.mark.parametrize("state, expected", [("notLoaded", UNAVAILABLE), ("active", QUEUED)])
def test_inspect_unavailable_and_active_do_not_update(tmp_path, state, expected):
    fake = Fakecall({"status": {"type": state}, "model": MODELS[0]})
    rpc, listener = rpc_with_socket(tmp_path, fake)
    try:
        status, mask, current = rpc.inspect("thread-1", 2)
        expected_result = (expected, 63, 1) if state == "active" else (expected, 0, 1)
        assert (status, mask, current) == expected_result
        assert not any(method == "thread/settings/update" for method, _ in fake.calls)
    finally:
        listener.close()


@pytest.mark.parametrize("target, model", [
    (1, "gpt-6-astra"), (2, "gpt-6-sol"), (3, "gpt-6-luna"),
    (4, "gpt-5.6-sol"), (5, "gpt-5.6-terra"), (6, "gpt-5.6-luna"),
])
def test_inspect_idle_updates_and_verifies(tmp_path, target, model):
    fake = Fakecall(
        {"status": {"type": "idle"}, "model": "gpt-5.5"},
        verified={"status": {"type": "idle"}, "model": model},
    )
    rpc, listener = rpc_with_socket(tmp_path, fake)
    try:
        assert rpc.inspect("thread-1", target) == (APPLIED, 63, target)
        updates = [params for method, params in fake.calls if method == "thread/settings/update"]
        assert updates == [{"threadId": "thread-1", "model": model}]
        assert [method for method, _ in fake.calls].count("thread/read") == 3
    finally:
        listener.close()


@pytest.mark.parametrize("catalog, verified, target, expected", [
    ([MODELS[0]], None, 2, FAILED),
    (MODELS, {"status": {"type": "idle"}, "model": MODELS[0]}, 2, FAILED),
])
def test_inspect_rejects_missing_catalog_or_failed_verify(
        tmp_path, catalog, verified, target, expected):
    fake = Fakecall({"status": {"type": "idle"}, "model": MODELS[0]}, catalog, verified)
    rpc, listener = rpc_with_socket(tmp_path, fake)
    try:
        assert rpc.inspect("thread-1", target)[0] == expected
        assert not any(method == "thread/settings/update" for method, _ in fake.calls) \
            if catalog == [MODELS[0]] else True
    finally:
        listener.close()


@dataclass
class hostSession:
    id: str
    short: str
    model: str = MODELS[0]
    turn_id: str = "turn-1"
    state: str = "working"


class AsyncRPC:
    def __init__(self, result, delay=0.01):
        self.results = list(result) if isinstance(result, list) else [result]
        self.delay = delay
        self.calls = []
        self.models = {}

    def observe(self, thread_ids):
        return {key: value for key, value in self.models.items() if key in thread_ids}

    def inspect(self, thread_id, target):
        self.calls.append((thread_id, target))
        time.sleep(self.delay)
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]

    def close(self):
        pass


def wait_control(control, sessions, now):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        control.poll(sessions, now)
        if control.future is None:
            return
        time.sleep(0.005)
    raise AssertionError("model-control worker did not finish")


def test_control_pins_context_and_target_across_rotation():
    rpc = AsyncRPC([(READY, 15, 1), (APPLIED, 15, 2)])
    control = ModelControl("unused", rpc)
    first = hostSession("a", "first")
    second = hostSession("b", "second", MODELS[1])
    try:
        view = control.context(first, 0)
        control.handle("open", view["context"], 7, 0, 0)
        selected = control.selection
        control.poll([first], 3)
        assert rpc.calls == [("a", 0)]
        wait_control(control, [first], 4)
        pinned = control.context(second, 5)
        assert pinned["context"] == selected.context
        assert pinned["request"] == 7
        control.handle("confirm", selected.context, 7, 2, 6)
        assert selected.status == QUEUED
        wait_control(control, [first], 9)
        assert rpc.calls[-1] == ("a", 2)
        assert control.applied["a"] == (MODELS[1], first.turn_id)
    finally:
        control.close()


def test_button_press_pins_displayed_conversation_before_five_second_rotation():
    control = ModelControl("unused", AsyncRPC((READY, 15, 1)))
    chooser = Chooser(mode="all")
    first = hostSession("a", "first")
    second = hostSession("b", "second", MODELS[1])
    sessions = [first, second]
    try:
        assert chooser.choose(sessions, 0) is first
        view = control.context(first, 4.99)
        control.handle("pin", view["context"], 9, 0, 4.99)
        pinned = control.pinned(5.1)
        assert pinned == first.id
        chooser.current, chooser.manual_until = pinned, 6.1
        assert chooser.choose(sessions, 5.1) is first
        selection = control.selection
        control.handle("open", view["context"], 9, 0, 5.9)
        assert control.selection is selection
        control.handle("cancel", view["context"], 9, 0, 6)
        assert control.pinned(6) is None
    finally:
        control.close()


def test_control_rejects_stale_context_and_ignores_unavailable_confirm():
    rpc = AsyncRPC((APPLIED, 15, 2))
    control = ModelControl("unused", rpc)
    session = hostSession("a", "first")
    try:
        view = control.context(session, 0)
        control.handle("open", view["context"], 1, 0, 0)
        selected = control.selection
        control.handle("confirm", selected.context + 1, 1, 2, 1)
        assert selected.status != QUEUED
        selected.status = UNAVAILABLE
        control.handle("confirm", selected.context, 1, 2, 1)
        assert not rpc.calls
    finally:
        control.close()


def test_control_marks_closed_target_gone():
    control = ModelControl("unused", AsyncRPC((APPLIED, 15, 2)))
    session = hostSession("a", "first")
    try:
        view = control.context(session, 0)
        control.handle("open", view["context"], 1, 0, 0)
        control.selection.status = READY
        control.selection.mask = 15
        control.handle("confirm", view["context"], 1, 2, 1)
        assert control.selection.status == QUEUED
        assert control.pending[session.id] is control.selection
        control.poll([], 3)
        assert control.selection.status == GONE
        assert not control.pending
    finally:
        control.close()


def test_control_retries_queued_job_after_two_seconds_then_applies():
    rpc = AsyncRPC([(READY, 15, 1), (QUEUED, 15, 1), (APPLIED, 15, 2)])
    control = ModelControl("unused", rpc)
    session = hostSession("a", "first")
    try:
        view = control.context(session, 0)
        control.handle("open", view["context"], 1, 0, 0)
        wait_control(control, [session], 3)
        control.handle("confirm", view["context"], 1, 2, 4)
        control.poll([session], 4)
        wait_control(control, [session], 5)
        assert rpc.calls[-1] == ("a", 2)
        assert control.selection.status == QUEUED
        control.poll([session], 5.5)
        assert rpc.calls == [("a", 0), ("a", 2)]
        control.poll([session], 7)
        wait_control(control, [session], 8)
        assert rpc.calls == [("a", 0), ("a", 2), ("a", 2)]
        assert control.selection.status == APPLIED
    finally:
        control.close()


def test_inspect_racing_active_readback_stays_queued(tmp_path):
    fake = Fakecall({"status": {"type": "idle"}, "model": MODELS[0]},
                    verified={"status": {"type": "active"}, "model": MODELS[1]})
    rpc, listener = rpc_with_socket(tmp_path, fake)
    try:
        assert rpc.inspect("thread-1", 2) == (QUEUED, 63, 2)
        assert [method for method, _ in fake.calls] == [
            "thread/read", "model/list", "thread/read", "thread/settings/update", "thread/read"]
    finally:
        listener.close()


def test_model_picker_packet_utf8_crc_and_bounds():
    title = "模型" * 30
    packet = model_picker_packet(0x10001, 2, 5, 3, 44, 65535, title)
    length, sequence = struct.unpack_from("<HH", packet, len(MAGIC))
    assert sequence == 1
    assert length == 730
    body = packet[len(MAGIC) + 4:-2]
    assert len(body) == 730
    kind, status, available, current, context, request, encoded_title = struct.unpack(
        "<BBBBIH48s", body[:58])
    assert (kind, status, available, current, context, request) == (7, 2, 5, 3, 44, 65535)
    assert int.from_bytes(packet[-2:], "little") == binascii.crc_hqx(packet[len(MAGIC):-2], 0xffff)
    assert encoded_title.rstrip(b"\0").decode("utf-8") == title.encode("utf-8")[:47].decode("utf-8", "ignore")
    bitmap = body[58:]
    assert len(bitmap) == 672
    assert bitmap == text_mask(title, align="left")
    with pytest.raises(ValueError):
        model_picker_packet(1, 8, 0, 0, 0, 0, "x")
    with pytest.raises(ValueError):
        model_picker_packet(1, 0, 64, 0, 0, 0, "x")
    with pytest.raises(ValueError):
        model_picker_packet(1, 0, 0, 7, 0, 0, "x")
    with pytest.raises(ValueError):
        model_picker_packet(1, 0, 0, 0, 0, 65536, "x")


def test_live_model_overrides_old_device_choice_without_new_turn():
    rpc = AsyncRPC((APPLIED, 15, 2))
    control = ModelControl("unused", rpc)
    session = hostSession("a", "first")
    try:
        control.applied["a"] = (MODELS[1], session.turn_id)
        rpc.models["a"] = MODELS[3]
        wait_control(control, [session], 0)
        control.sync_models([session])
        assert session.model == MODELS[3]
        assert not control.applied
        rpc.models["a"] = MODELS[0]
        wait_control(control, [session], .3)
        control.sync_models([session])
        assert session.model == MODELS[0]
        assert not rpc.calls  # Reads never submit an update or a turn.
    finally:
        control.close()


def test_closed_selection_unpins_and_drops_cached_model():
    control = ModelControl("unused", AsyncRPC((READY, 15, 1)))
    session = hostSession("a", "first")
    try:
        ctx = control.context(session, 0)
        control.handle("open", ctx["context"], 1, 0, 0)
        control.observed[session.id] = MODELS[3]
        control.poll([], .2)
        assert control.pinned(.2) is None
        assert control.selection.status == GONE
        assert not control.observed
    finally:
        control.close()


def test_observe_reads_only_loaded_threads_and_never_subscribes(tmp_path):
    calls = []
    def call(method, params):
        calls.append((method, params))
        if method == "thread/loaded/list":
            return {"data": ["a"]}
        if method == "thread/read":
            return {"thread": {"model": MODELS[2], "status": {"type": "idle"}}}
        raise AssertionError(method)
    rpc, listener = rpc_with_socket(tmp_path, call)
    try:
        assert rpc.observe(["a", "unloaded"]) == {"a": MODELS[2]}
        assert calls == [("thread/loaded/list", {"limit": 100, "cursor": None}),
                         ("thread/read", {"threadId": "a", "includeTurns": False})]
    finally:
        listener.close()


def test_newer_codex_choice_cancels_an_older_queued_device_choice():
    rpc = AsyncRPC([(READY, 15, 1), (QUEUED, 15, 1)])
    control = ModelControl("unused", rpc)
    session = hostSession("a", "first")
    try:
        view = control.context(session, 0)
        control.handle("open", view["context"], 1, 0, 0)
        wait_control(control, [session], 0)
        control.handle("confirm", view["context"], 1, 2, .1)
        wait_control(control, [session], .1)
        assert control.pending
        rpc.models["a"] = MODELS[3]
        wait_control(control, [session], .4)
        control.sync_models([session])
        assert not control.pending
        assert session.model == MODELS[3]
    finally:
        control.close()


class DelayedConnection:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.current = MODELS[0]
        self.waits = {}

    def send(self, raw):
        self.message = json.loads(raw)
        self.calls.append(self.message)

    def recv(self, timeout):
        method = self.message["method"]
        self.waits[method] = timeout
        delay = 6 if method in ("model/list", "thread/settings/update") else 0
        if timeout < delay:
            self.clock[0] += timeout
            raise TimeoutError("delayed model refresh")
        self.clock[0] += delay
        if method == "model/list":
            result = {"data": [{"model": model} for model in MODELS]}
        elif method == "thread/settings/update":
            self.current = self.message["params"]["model"]
            result = {}
        elif method == "thread/read":
            result = {"thread": {"model": self.current, "status": {"type": "idle"}}}
        else:
            raise AssertionError(method)
        return json.dumps({"id": self.message["id"], "result": result})

    def close(self):
        pass


def test_slow_realistic_catalog_and_settings_are_not_cut_off_at_five_seconds(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr(model_control.time, "monotonic", lambda: clock[0])
    rpc, _ = rpc_with_socket(tmp_path, None)
    del rpc.call  # Exercise real framing, deadlines and response matching.
    connection = rpc.connection = DelayedConnection(clock)
    assert rpc.inspect("chosen") == (READY, 63, 1)
    assert clock[0] == 6
    assert rpc.inspect("chosen", 3) == (APPLIED, 63, 3)
    assert clock[0] == 12
    assert sum(row["method"] == "model/list" for row in connection.calls) == 1
    assert connection.waits["model/list"] == 20
    assert connection.waits["thread/settings/update"] == 20
    assert connection.waits["thread/read"] == 5


def test_catalog_cache_expires_and_server_replacement_invalidates_it(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr(model_control.time, "monotonic", lambda: clock[0])
    fake = Fakecall({"status": {"type": "idle"}, "model": MODELS[0]})
    rpc, _ = rpc_with_socket(tmp_path, fake)
    assert rpc.available_mask() == 63
    fake.catalog = [MODELS[0]]
    clock[0] = CATALOG_TTL - 1
    assert rpc.available_mask() == 63
    clock[0] = CATALOG_TTL
    assert rpc.available_mask() == 1
    assert sum(method == "model/list" for method, _ in fake.calls) == 2
    fake.catalog = [MODELS[3]]
    rpc.socket_path.stat = lambda: SimpleNamespace(st_dev=1, st_ino=20, st_mtime_ns=30)
    assert rpc.available_mask() == 8


def test_catalog_is_not_cached_until_all_pages_succeed(tmp_path):
    calls = []
    def incomplete(method, params):
        calls.append(params)
        if not params["cursor"]:
            return {"data": [{"model": MODELS[0]}], "nextCursor": "next"}
        raise TimeoutError("second page delayed")
    rpc, _ = rpc_with_socket(tmp_path, incomplete)
    with pytest.raises(TimeoutError):
        rpc.available_mask()
    assert rpc.catalog is None
    assert [row["cursor"] for row in calls] == [None, "next"]


def test_thread_becomes_active_during_catalog_refresh_is_queued(tmp_path):
    refreshed = False
    calls = []
    def call(method, params):
        nonlocal refreshed
        calls.append(method)
        if method == "thread/read":
            return {"thread": {"model": MODELS[0], "status": {
                "type": "active" if refreshed else "idle"}}}
        assert method == "model/list"
        refreshed = True
        return {"data": [{"model": model} for model in MODELS]}
    rpc, _ = rpc_with_socket(tmp_path, call)
    assert rpc.inspect("chosen", 2) == (QUEUED, 63, 1)
    assert calls == ["thread/read", "model/list", "thread/read"]


def test_already_applied_target_does_not_send_another_update(tmp_path):
    fake = Fakecall({"status": {"type": "idle"}, "model": MODELS[2]})
    rpc, _ = rpc_with_socket(tmp_path, fake)
    assert rpc.inspect("chosen", 3) == (APPLIED, 63, 3)
    assert not any(method == "thread/settings/update" for method, _ in fake.calls)


def test_timeout_diagnostic_names_stage_and_does_not_dump_requests(tmp_path, capsys):
    rpc, _ = rpc_with_socket(tmp_path, None)
    del rpc.call
    class Broken(FakeConnection):
        def send(self, raw):
            pass
        def recv(self, timeout):
            raise TimeoutError("internal server details")
    rpc.connection = Broken()
    assert rpc.inspect("private-thread") == (FAILED, 0, 0)
    assert "stage=thread/read" in rpc.last_error
    output = capsys.readouterr().out
    assert "timed out after 5s" in output
    assert "private-thread" not in output and "internal server details" not in output


def test_server_error_preserves_method_and_code_without_logging_error_body(tmp_path):
    rpc = ModelRPC(tmp_path)
    class Rejected(FakeConnection):
        def send(self, raw):
            self.request = json.loads(raw)
        def recv(self, timeout):
            return json.dumps({"id": self.request["id"], "error": {
                "code": -32602, "message": "private upstream content"}})
    rpc.connection = Rejected()
    with pytest.raises(RPCRejected, match=r"thread/settings/update rejected \(code=-32602\)") as error:
        rpc.call("thread/settings/update", {"threadId": "a", "model": MODELS[0]})
    assert "private upstream content" not in str(error.value)
