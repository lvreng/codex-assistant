#!/usr/bin/env python3
"""Check resume permission semantics with isolated Codex stdio, without inference."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))
from resume_permissions import prepare_resume


class Server:
    def __init__(self, binary, directory):
        self.binary = binary
        self.directory = directory
        self.sequence = 0
        self.process = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            self.binary, "app-server", cwd=self.directory,
            env={**os.environ, "CODEX_HOME": self.directory,
                 "CODEX_PET_ACCOUNT_READER": "1"},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=8 * 1024 * 1024,
            start_new_session=True)
        await self.call("initialize", {
            "clientInfo": {"name": "pet_resume_smoke", "version": "1"},
            "capabilities": {"experimentalApi": True}})
        await self.send({"method": "initialized"})

    async def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, params, policy=None):
        self.sequence += 1
        message = {"id": self.sequence, "method": method, "params": params}
        await self.send(policy.client(message) if policy else message)
        async with asyncio.timeout(20):
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError("isolated app-server closed stdout")
                reply = json.loads(line)
                if reply.get("id") != self.sequence:
                    continue
                if policy:
                    reply = policy.server(reply)
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply["result"]

    async def close(self):
        if self.process:
            # The npm launcher has a native child which also owns the stdio pipes.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                os.killpg(self.process.pid, signal.SIGKILL)
                await asyncio.wait_for(self.process.wait(), timeout=5)


async def main():
    binary = sys.argv[1] if len(sys.argv) > 1 else str(Path.home() / ".npm-global/bin/codex")
    with tempfile.TemporaryDirectory(prefix="pet-resume-smoke-") as directory:
        server = Server(binary, directory)
        try:
            await server.start()
            original = await server.call("thread/start", {
                "cwd": directory, "sandbox": "read-only", "approvalPolicy": "on-request"})
            thread = original["thread"]["id"]
            # Materialize a disposable rollout; never call turn/start or any model endpoint.
            await server.call("thread/inject_items", {"threadId": thread, "items": [{
                "type": "message", "role": "user", "content": [{"type": "input_text",
                "text": "Local resume test fixture; no inference should be started."}]}]})
            await server.close()
            print("[1/4] Temporary history created; original isolated server stopped", flush=True)
            await server.start()
            args, policy = prepare_resume(["--yolo", "resume", "--all"])
            assert args == ["resume", "--all"]
            resumed = await server.call("thread/resume", {"threadId": thread}, policy)
            assert resumed["approvalPolicy"] == "never"
            assert resumed["sandbox"]["type"] == "dangerFullAccess" and policy.applied
            print("[2/4] Real resume returned approvals=never, sandbox=dangerFullAccess", flush=True)
            await server.call("thread/settings/update", {"threadId": thread, "model": "gpt-6-sol"})
            readback = await server.call("thread/read", {"threadId": thread, "includeTurns": False})
            assert readback["thread"]["model"] == "gpt-6-sol"
            print("[3/4] Resumed thread still accepts device model-control settings", flush=True)
            another = await server.call("thread/start", {
                "cwd": directory, "sandbox": "read-only", "approvalPolicy": "on-request"})
            assert another["approvalPolicy"] == "on-request"
            assert another["sandbox"]["type"] == "readOnly"
            print("[4/4] Other thread permissions unchanged; no inference requested", flush=True)
        finally:
            await server.close()


if __name__ == "__main__":
    asyncio.run(main())
