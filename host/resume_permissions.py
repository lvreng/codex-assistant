"""Carry an explicit --yolo resume through the App Server resume API."""
from codex_default import FLAGS, VALUES, interactive


YOLO = {"--yolo", "--dangerously-bypass-approvals-and-sandbox"}


def prepare_resume(args):
    """Remove only actual YOLO options on resume, never option values/prompts."""
    args = list(args)
    if not interactive(args):
        return args, None
    command = None
    positions = set()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            break
        if arg in VALUES:
            i += 2
            continue
        if arg.split("=", 1)[0] in VALUES or (
                len(arg) > 2 and arg[:2] in VALUES and not arg.startswith("--")):
            i += 1
            continue
        if arg in YOLO:
            positions.add(i)
        elif arg not in FLAGS and command is None:
            command = arg
            if command != "resume":
                break
        i += 1
    if command != "resume" or not positions:
        return args, None
    return [arg for i, arg in enumerate(args) if i not in positions], ResumePermissions()


class ResumePermissions:
    """Only the first successful, user-selected resume gets the explicit policy."""

    def __init__(self):
        self.pending = None
        self.applied = False

    def client(self, message):
        if (self.applied or self.pending is not None
                or message.get("method") != "thread/resume" or "id" not in message):
            return message
        params = message.get("params")
        if not isinstance(params, dict) or not params.get("threadId"):
            return message
        self.pending = message["id"]
        params = {**params, "approvalPolicy": "never", "sandbox": "danger-full-access"}
        # A named profile and the legacy sandbox selector are mutually exclusive.
        params.pop("permissions", None)
        return {**message, "params": params}

    def server(self, message):
        if (self.pending is None or message.get("id") != self.pending
                or not ("result" in message or "error" in message)):
            return message
        self.pending = None
        if "error" in message:
            return message  # Keep managed-policy rejections intact; never retry weaker.
        result = message.get("result") or {}
        sandbox = result.get("sandbox") or {}
        if result.get("approvalPolicy") != "never" or sandbox.get("type") != "dangerFullAccess":
            return {"id": message["id"], "error": {
                "code": -32602,
                "message": "ESP32 launcher: the server did not apply the requested --yolo "
                           "permissions. Resume stopped; no automatic permission fallback.",
            }}
        self.applied = True
        return message
