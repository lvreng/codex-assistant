#!/usr/bin/python3
"""Default interactive Codex entry; automation and explicit remotes pass through."""
import os
from pathlib import Path
import sys

COMMANDS = set("agents exec e review login logout mcp plugin app-server remote-control "
               "completion update doctor sandbox debug apply a queue archive delete "
               "migrate-rollouts unarchive cloud exec-server features help".split())
VALUES = set("-c --config --enable --disable -i --image -m --model --local-provider "
             "-p --profile -s --sandbox -C --cd --add-dir -a --ask-for-approval".split())
FLAGS = set("--strict-config --oss --approve-for-me --dangerously-bypass-approvals-and-sandbox "
            "--dangerously-bypass-hook-trust --yolo --full-auto --worktree --search "
            "--no-alt-screen --last --all".split())


def interactive(args):
    command = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg in ("-h", "--help", "-V", "--version", "--remote", "--remote-auth-token-env"):
            return False
        if arg.startswith(("--remote=", "--remote-auth-token-env=")):
            return False
        if arg in VALUES:
            i += 2
            continue
        if arg.split("=", 1)[0] in VALUES or (
                len(arg) > 2 and arg[:2] in VALUES and not arg.startswith("--")):
            i += 1
            continue
        if arg in FLAGS:
            i += 1
            continue
        if arg.startswith("-"):
            return False  # Let Codex validate unfamiliar flags without changing execution mode.
        if command is None:
            if arg in COMMANDS:
                return False
            if arg not in ("resume", "fork"):
                break  # This is a prompt, not a subcommand.
            command = arg
        i += 1
    return True


def main():
    args = sys.argv[1:]
    binary = os.environ.get("CODEX_PET_REAL_CODEX", str(Path.home() / ".npm-global/bin/codex"))
    if os.environ.get("CODEX_PET_BYPASS") == "1" or not interactive(args):
        os.execv(binary, [binary, *args])
    script = Path(__file__).resolve().with_name("codex-device.sh")
    os.execv("/bin/bash", ["bash", str(script), *args])


if __name__ == "__main__":
    main()
