import asyncio
import json
import os
from pathlib import Path

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve

from client_presence import Presence, visible_locks, process_identity
from codex_client import relay


def attached(presence, tid, request=1):
    presence.client({"id": request, "method": "thread/resume", "params": {"threadId": tid}})
    presence.server({"id": request, "result": {"thread": {"id": tid}}})


def test_exit_immediate_despite_retained_server_lock(tmp_path):
    presence = Presence(tmp_path / "pet-client-presence", os.getpid())
    attached(presence, "a")
    locks = {"a": os.getpid(), "unmanaged": os.getpid()}
    assert visible_locks(locks, tmp_path) == locks
    presence.close()
    assert visible_locks(locks, tmp_path) == {"unmanaged": os.getpid()}


def test_second_window_for_same_thread_keeps_it_visible(tmp_path):
    first = Presence(tmp_path / "pet-client-presence", os.getpid())
    second = Presence(tmp_path / "pet-client-presence", os.getpid())
    attached(first, "a")
    attached(second, "a")
    first.close()
    locks = {"a": os.getpid()}
    assert visible_locks(locks, tmp_path) == locks
    second.close()
    assert visible_locks(locks, tmp_path) == {}


def test_unsubscribe_new_chat_failed_resume_and_foreign_notifications(tmp_path):
    presence = Presence(tmp_path / "pet-client-presence", os.getpid())
    attached(presence, "a")
    presence.client({"id": 2, "method": "thread/unsubscribe", "params": {"threadId": "a"}})
    presence.server({"id": 2, "result": {"status": "unsubscribed"}})
    presence.client({"id": 3, "method": "thread/start", "params": {}})
    presence.server({"id": 3, "result": {"thread": {"id": "b"}}})
    presence.client({"id": 4, "method": "thread/resume", "params": {"threadId": "c"}})
    presence.server({"id": 4, "error": {"code": -1}})
    presence.server({"method": "thread/started", "params": {"thread": {"id": "other"}}})
    assert presence.active == {"b"}
    assert presence.known == {"a", "b"}
    locks = {name: os.getpid() for name in ("a", "b")}
    assert visible_locks(locks, tmp_path) == {"b": os.getpid()}


def test_dead_or_reused_client_pid_is_not_live(tmp_path):
    presence = Presence(tmp_path / "pet-client-presence", os.getpid())
    attached(presence, "a")
    data = json.loads(presence.path.read_text())
    data["start"] = "not-the-same-process"
    presence.path.write_text(json.dumps(data))
    assert visible_locks({"a": os.getpid()}, tmp_path) == {}
    data["server_start"] = "previous-server"
    presence.path.write_text(json.dumps(data))
    assert visible_locks({"a": os.getpid()}, tmp_path) == {"a": os.getpid()}


def test_last_legacy_client_exit_filters_only_background_server(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    server, client = proc / "71", proc / "72"
    for path, args in ((server, b"codex\0app-server\0"), (client, b"codex\0resume\0")):
        path.mkdir()
        (path / "comm").write_text("codex\n")
        (path / "cmdline").write_bytes(args)
        (path / "stat").write_text(f"{path.name} (codex) S " + "0 " * 18 + "123\n")
    (server / "environ").write_bytes(b"CODEX_PET_BACKGROUND_SERVER=1\0")
    assert process_identity(72, proc) == "123"
    assert visible_locks({"a": 71}, tmp_path, proc) == {"a": 71}
    (client / "comm").unlink()
    assert visible_locks({"a": 71}, tmp_path, proc) == {}


def test_relay_preserves_messages_and_tracks_disconnect(tmp_path):
    async def run():
        upstream, downstream = tmp_path / "up.sock", tmp_path / "down.sock"
        directory = tmp_path / "pet-client-presence"
        seen = []
        async def server(ws):
            async for raw in ws:
                seen.append(raw)
                message = json.loads(raw)
                await ws.send(json.dumps({"id": message["id"], "result": {"thread": {"id": "a"}}}))
        async with unix_serve(server, str(upstream), compression=None):
            async with unix_serve(lambda ws: relay(ws, upstream, directory), str(downstream), compression=None):
                async with unix_connect(str(downstream), compression=None) as ws:
                    raw = '{ "id": 1, "method": "thread/resume", "params": {"threadId":"a"}}'
                    await ws.send(raw)
                    assert json.loads(await ws.recv())["result"]["thread"]["id"] == "a"
                    assert seen == [raw]
                    assert visible_locks({"a": os.getpid()}, tmp_path) == {"a": os.getpid()}
                for _ in range(50):
                    if not visible_locks({"a": os.getpid()}, tmp_path):
                        break
                    await asyncio.sleep(.01)
                assert not visible_locks({"a": os.getpid()}, tmp_path)
        records = list(directory.glob("*.json"))
        assert len(records) == 1
        assert set(json.loads(records[0].read_text())) == {
            "pid", "start", "server_pid", "server_start", "known", "active"}
    asyncio.run(run())
