from pathlib import Path
import subprocess
import sys

import pytest

from codex_default import interactive
import codex_default
import control_server


@pytest.mark.parametrize("args", [
    [], ["--yolo", "resume", "--all"], ["resume", "--last"], ["fork", "thread"],
    ["--model", "gpt-6-astra", "resume", "--all"], ["-c", 'model="exec"', "resume"],
    ["-pgpt", "--no-alt-screen"], ["-C", "/tmp/a b", "hello world"],
    ["--", "exec"], ["please review this"], ["--config=x=true", "fork"],
])
def test_interactive_default(args):
    assert interactive(args)


@pytest.mark.parametrize("args", [
    ["exec", "resume", "--last"], ["e", "test"], ["--yolo", "exec", "test"],
    ["app-server", "--listen", "unix:///tmp/sock"], ["login", "status"],
    ["update"], ["doctor"], ["--help"], ["resume", "--help"], ["--version"],
    ["--remote", "unix:///tmp/socket", "resume"], ["resume", "--remote=unix:///tmp/s"],
    ["--unknown-future-flag"], ["-c", "x=true", "mcp", "list"], ["review"],
])
def test_automation_and_explicit_remote_passthrough(args):
    assert not interactive(args)


def test_entry_keeps_arguments_and_working_directory(monkeypatch):
    calls = []
    class Executed(Exception):
        pass
    def execute(path, args):
        calls.append((path, args, Path.cwd()))
        raise Executed
    monkeypatch.setattr(codex_default.os, "execv", execute)
    monkeypatch.setenv("CODEX_PET_REAL_CODEX", "/original/codex")
    monkeypatch.delenv("CODEX_PET_BYPASS", raising=False)
    args = ["--yolo", "-C", "/some path", "resume", "--all"]
    monkeypatch.setattr(sys, "argv", ["codex", *args])
    with pytest.raises(Executed):
        codex_default.main()
    assert calls[0][0] == "/bin/bash"
    assert calls[0][1][2:] == args
    assert calls[0][2] == Path.cwd()
    monkeypatch.setenv("CODEX_PET_BYPASS", "1")
    with pytest.raises(Executed):
        codex_default.main()
    assert calls[-1][:2] == ("/original/codex", ["/original/codex", *args])


def test_start_reuses_a_healthy_server_without_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(control_server, "ready", lambda *_: True)
    monkeypatch.setattr(control_server.subprocess, "run", lambda *_a, **_k: pytest.fail("must reuse"))
    control_server.ensure("/original/codex", tmp_path, tmp_path / "control/server.sock")


def test_start_verifies_protocol_even_with_a_stale_socket(tmp_path, monkeypatch):
    checks = iter([False, True])
    calls = []
    def run(args, **_):
        calls.append(args)
        return subprocess.CompletedProcess(args, 3 if "is-active" in args else 0)
    monkeypatch.setattr(control_server, "ready", lambda *_: next(checks))
    monkeypatch.setattr(control_server.subprocess, "run", run)
    control_server.ensure("/original/codex", tmp_path, tmp_path / "control/server.sock")
    assert calls[-1][0] == "systemd-run"
    assert calls[-1][-4:] == ["/original/codex", "app-server", "--listen",
                             f"unix://{tmp_path}/control/server.sock"]
    assert not any("stop" in args or "restart" in args for args in calls)


def test_start_waits_for_an_existing_startup(tmp_path, monkeypatch):
    checks = iter([False, False, True])
    calls = []
    def run(args, **_):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(control_server, "ready", lambda *_: next(checks))
    monkeypatch.setattr(control_server.subprocess, "run", run)
    monkeypatch.setattr(control_server.time, "sleep", lambda _: None)
    control_server.ensure("/original/codex", tmp_path, tmp_path / "control/server.sock")
    assert len(calls) == 1 and "is-active" in calls[0]
