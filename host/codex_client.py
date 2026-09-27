"""Launch a TUI over a private Unix WebSocket presence relay."""
import asyncio
import json
import os
from pathlib import Path
import socket
import signal
import struct
import sys
import tempfile

from websockets.asyncio.client import unix_connect
from websockets.asyncio.server import unix_serve
from websockets.exceptions import ConnectionClosed

from client_presence import Presence
from resume_permissions import prepare_resume


async def forward(source, destination, observe, rewrite=None):
    async for data in source:
        try:
            message = json.loads(data)
        except (ValueError, TypeError):
            message = None
        if isinstance(message, dict):
            if rewrite is not None:
                updated = rewrite(message)
                if updated is not message:
                    data = json.dumps(updated)
                    message = updated
            try:
                observe(message)
            except (ValueError, TypeError, OSError):
                pass  # Presence failure must never alter the Codex exchange.
        await destination.send(data)


async def relay(client, upstream, directory, permissions=None):
    async with unix_connect(str(upstream), compression=None, max_size=None) as server:
        peer = server.transport.get_extra_info("socket").getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        presence = Presence(directory, struct.unpack("3i", peer)[0])

        tasks = [asyncio.create_task(forward(client, server, presence.client,
                                            permissions.client if permissions else None)),
                 asyncio.create_task(forward(server, client, presence.server,
                                             permissions.server if permissions else None))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                presence.close()
            except OSError:
                pass


async def main(binary, upstream, *args):
    args, permissions = prepare_resume(args)
    if permissions is not None:
        print("ESP32 control: requesting --yolo for the selected resumed thread "
              "(approvals=never, sandbox=danger-full-access).", file=sys.stderr, flush=True)
    codex_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    with tempfile.TemporaryDirectory(prefix="codex-pet-client-") as temp:
        endpoint = Path(temp) / "client.sock"

        async def connected(client):
            try:
                await relay(client, upstream, codex_dir / "pet-client-presence", permissions)
            except (ConnectionClosed, OSError):
                await client.close()

        async with unix_serve(connected, str(endpoint), compression=None, max_size=None):
            process = await asyncio.create_subprocess_exec(
                binary, "--remote", f"unix://{endpoint}", *args)
            # The foreground TUI handles Ctrl-C itself; don't kill its relay.
            loop = asyncio.get_running_loop()
            loop.add_signal_handler(signal.SIGINT, lambda: None)
            loop.add_signal_handler(signal.SIGTERM, lambda: process.terminate()
                                    if process.returncode is None else None)
            try:
                return await process.wait()
            finally:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:])))
