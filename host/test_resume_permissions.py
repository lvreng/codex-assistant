import asyncio
import json

import pytest

import codex_client
from codex_client import forward
from resume_permissions import prepare_resume, ResumePermissions


@pytest.mark.parametrize("args,expected", [
    (["--yolo", "resume", "--all"], ["resume", "--all"]),
    (["resume", "--yolo", "--last"], ["resume", "--last"]),
    (["--dangerously-bypass-approvals-and-sandbox", "resume", "id"], ["resume", "id"]),
    (["--yolo", "-C", "/some path", "resume", "id", "prompt"],
     ["-C", "/some path", "resume", "id", "prompt"]),
    (["-c", 'model="resume"', "resume", "--yolo", "--", "--yolo"],
     ["-c", 'model="resume"', "resume", "--", "--yolo"]),
    (["--yolo", "--model=astra", "-pmyprofile", "resume", "--all"],
     ["--model=astra", "-pmyprofile", "resume", "--all"]),
])
def test_resume_moves_only_explicit_yolo_flag(args, expected):
    clean, policy = prepare_resume(args)
    assert clean == expected
    assert isinstance(policy, ResumePermissions)


@pytest.mark.parametrize("args", [
    [], ["--yolo"], ["--yolo", "new prompt"], ["--yolo", "fork", "id"],
    ["resume", "--all"], ["resume", "--", "--yolo"],
    ["--", "resume", "--yolo"], ["-C", "resume", "--yolo"],
    ["-m", "--yolo", "resume"], ["--model=--yolo", "resume"],
    ["--yolo", "exec", "resume"], ["--yolo", "resume", "--help"],
    ["--yolo", "--remote=unix:///tmp/explicit", "resume"],
    ["--yolo", "--future-flag", "resume"],
])
def test_other_invocations_are_unchanged(args):
    assert prepare_resume(args) == (args, None)


def request(request_id=1, method="thread/resume"):
    return {"id": request_id, "method": method,
            "params": {"threadId": "selected", "excludeTurns": True, "model": "model"}}


def success(request_id=1):
    return {"id": request_id, "result": {"thread": {"id": "selected"},
            "approvalPolicy": "never", "sandbox": {"type": "dangerFullAccess"}}}


def test_resume_permissions_exact_selected_thread_once():
    policy = ResumePermissions()
    for method in ("initialize", "thread/list", "thread/read", "thread/start", "thread/fork",
                   "thread/settings/update", "turn/start"):
        msg = request(method=method)
        assert policy.client(msg) is msg
    msg = request()
    patched = policy.client(msg)
    assert patched == {**msg, "params": {**msg["params"],
                       "approvalPolicy": "never", "sandbox": "danger-full-access"}}
    assert "sandbox" not in msg["params"]
    unrelated = {"id": 99, "result": {}}
    assert policy.server(unrelated) is unrelated
    assert policy.pending == 1
    result = success()
    assert policy.server(result) is result
    assert policy.applied
    later = request(2)
    assert policy.client(later) is later


def test_named_profile_is_not_sent_together_with_sandbox():
    policy = ResumePermissions()
    msg = request()
    msg["params"]["permissions"] = "workspace"
    assert "permissions" not in policy.client(msg)["params"]


def test_server_rejection_is_unchanged_and_never_retried_implicitly():
    policy = ResumePermissions()
    policy.client(request())
    rejection = {"id": 1, "error": {"code": -32600, "message": "managed policy disallows"}}
    assert policy.server(rejection) is rejection
    assert not policy.applied
    assert policy.pending is None
    # Only a new request from the user can attempt another resume.
    assert policy.client(request(2))["params"]["sandbox"] == "danger-full-access"


@pytest.mark.parametrize("result", [
    {}, {"approvalPolicy": "on-request", "sandbox": {"type": "dangerFullAccess"}},
    {"approvalPolicy": "never", "sandbox": {"type": "workspaceWrite"}},
])
def test_mismatched_policy_is_not_reported_as_success(result):
    policy = ResumePermissions()
    policy.client(request())
    rejected = policy.server({"id": 1, "result": result})
    assert rejected["id"] == 1 and "error" in rejected and "result" not in rejected
    assert not policy.applied


def test_other_permission_options_are_not_silently_removed():
    args = ["--yolo", "resume", "-a", "on-request", "-s", "read-only"]
    clean, _ = prepare_resume(args)
    assert clean == args[1:]  # Native CLI still validates unsupported/conflicting options.


class Messages:
    def __init__(self, messages=()):
        self.messages = messages
        self.sent = []

    async def __aiter__(self):
        for message in self.messages:
            yield message

    async def send(self, message):
        self.sent.append(message)


def test_forward_unchanged_messages_byte_for_byte_even_if_presence_fails():
    raw = ['{ "id": 1, "method": "thread/read" }', b'{}', '[]', 'not-json']
    destination = Messages()
    def failed_presence(_):
        raise OSError("read-only")
    asyncio.run(forward(Messages(raw), destination, failed_presence, ResumePermissions().client))
    assert destination.sent == raw


def test_forward_checks_policy_before_presence_sees_success():
    policy = ResumePermissions()
    destination = Messages()
    observed = []
    asyncio.run(forward(Messages([json.dumps(request())]), destination,
                        observed.append, policy.client))
    assert json.loads(destination.sent[0])["params"]["sandbox"] == "danger-full-access"
    assert observed[0]["params"]["approvalPolicy"] == "never"
    destination = Messages()
    observed.clear()
    asyncio.run(forward(Messages([json.dumps({"id": 1, "result": {}})]), destination,
                        observed.append, policy.server))
    assert "error" in observed[0]
    assert "error" in json.loads(destination.sent[0])


def test_launcher_passes_compatible_argv_and_attaches_resume_policy(tmp_path, monkeypatch, capsys):
    launched = []
    handlers = []
    policies = []

    class Listener:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

    def serve(handler, *_args, **_kwargs):
        handlers.append(handler)
        return Listener()

    class Process:
        returncode = None

        async def wait(self):
            await handlers[0](object())
            self.returncode = 0
            return 0

    async def spawn(*args):
        launched.append(args)
        return Process()

    async def relay(client, upstream, directory, policy):
        assert upstream == "/tmp/shared.sock"
        assert directory == tmp_path / "pet-client-presence"
        policies.append(policy)

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(codex_client, "unix_serve", serve)
    monkeypatch.setattr(codex_client.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(codex_client, "relay", relay)
    assert asyncio.run(codex_client.main("/original/codex", "/tmp/shared.sock",
                                        "--yolo", "resume", "--all")) == 0
    assert launched[0][:2] == ("/original/codex", "--remote")
    assert launched[0][2].startswith("unix://")
    assert launched[0][3:] == ("resume", "--all")
    assert isinstance(policies[0], ResumePermissions)
    assert "requesting --yolo" in capsys.readouterr().err
