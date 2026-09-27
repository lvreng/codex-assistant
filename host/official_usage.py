"""Read-only official account telemetry through Codex's public app-server RPC.

No inference, conversation resume, credential extraction or billing scraping.
The helper process only serves account reads; token samples come from CLI logs.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import tomllib

from usage import MoreCodeReader, Usage, UNKNOWN_U32, UNKNOWN_U64

SOURCES = ("auto", "official", "morecode")
INTERVAL = 2.0


def account_usage(account, limits):
    account = account if isinstance(account, dict) else {}
    kind = account.get("type")
    plan = str(account.get("planType") or "")
    limits = limits if isinstance(limits, dict) else {}
    buckets = limits.get("rateLimitsByLimitId") or {}
    bucket = buckets.get("codex") if isinstance(buckets, dict) else None
    bucket = bucket or limits.get("rateLimits") or {}
    plan = str(bucket.get("planType") or plan)
    weekly = next((window for key in ("primary", "secondary")
                   if isinstance(window := bucket.get(key), dict)
                   and window.get("windowDurationMins") == 10080), {})
    remaining, reset = 65535, ""
    used = weekly.get("usedPercent")
    if kind == "chatgpt" and isinstance(used, (int, float)) and math.isfinite(used):
        remaining = round((100 - min(100, max(0, used))) * 10)
        stamp = weekly.get("resetsAt")
        if isinstance(stamp, (float, int)) and stamp > 0:
            try:
                reset = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M")
            except (ValueError, OSError, OverflowError):
                pass
    names = {"pro": "Pro", "plus": "Plus", "free": "Free", "go": "Go",
             "team": "Team", "business": "Business", "enterprise": "Enterprise",
             "edu": "Edu", "prolite": "Pro Lite"}
    return Usage(api_source="official", account_kind=1 if kind == "chatgpt" else
                 2 if kind == "apiKey" else 0, plan_name=names.get(plan, plan)[:20],
                 week_remaining_x10=remaining, week_reset=reset, stale=False)


class AccountRPC:
    def __init__(self, codex_dir):
        self.codex_dir = Path(codex_dir)
        self.process = None
        self.sequence = 0
        self.messages = queue.Queue()
        self.reader = None
        self.auth_stamp = None
        self.next_limits_request = 0.0

    def start(self):
        binary = shutil.which("codex") or str(Path.home() / ".npm-global/bin/codex")
        env = {**os.environ, "CODEX_HOME": str(self.codex_dir),
               "CODEX_PET_ACCOUNT_READER": "1"}
        # Select the official provider without touching the user's config.
        for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY"):
            env.pop(name, None)
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [binary, "app-server", "--stdio",
             "-c", 'model_provider="openai"'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=self.codex_dir)
        stream, messages = self.process.stdout, self.messages

        def read():
            try:
                for line in stream:
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    # Only replies, never model output or unsolicited account data.
                    if isinstance(message, dict) and "id" in message and "method" not in message:
                        messages.put(message)
            finally:
                messages.put(None)

        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        self.call("initialize", {"clientInfo": {"name": "codex_pet_usage",
                  "version": "1.0"}, "capabilities": {"experimentalApi": True}})
        self.process.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        self.process.stdin.flush()

    def call(self, method, params=None):
        self.sequence += 1
        seq = self.sequence
        self.process.stdin.write(json.dumps({"id": seq, "method": method,
                                            "params": params}) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                message = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError("official account read timed out") from None
            if message is None:
                raise RuntimeError("official account reader stopped")
            if message.get("id") == seq:
                if "error" in message:
                    # Never relay server payloads or account identifiers into logs.
                    raise RuntimeError("official account read rejected")
                return message.get("result") or {}
            if time.monotonic() >= deadline:
                raise TimeoutError("official account read timed out")

    def fetch(self):
        try:
            auth = (self.codex_dir / "auth.json").stat()
            stamp = (auth.st_ino, auth.st_mtime_ns)
        except OSError:
            stamp = None
        if stamp != self.auth_stamp:
            self.close()
            self.auth_stamp = stamp
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.start()
        account = self.call("account/read", {"refreshToken": False}).get("account")
        limits = {}
        if account and account.get("type") == "chatgpt":
            delay = self.next_limits_request - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.next_limits_request = time.monotonic() + INTERVAL
            limits = self.call("account/rateLimits/read", {"excludeResetCreditDetails": True})
        return account_usage(account, limits)

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if self.reader:
            self.reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


class OfficialReader:
    def __init__(self, codex_dir, rpc=None):
        self.rpc = rpc or AccountRPC(codex_dir)
        self.cached = Usage(api_source="official")
        self.next_refresh = 0.0
        self.future = None
        self.failures = 0
        self.error = None
        self.last_success = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="official-usage")

    def snapshot(self, now):
        if self.future is not None and self.future.done():
            try:
                self.cached = self.future.result()
                self.failures = 0
                self.error = None
                self.last_success = now
            except Exception as exc:
                self.cached = replace(self.cached, stale=True)
                self.failures += 1
                self.error = type(exc).__name__
            self.future = None
            if self.failures:
                self.next_refresh = now + min(60, INTERVAL * 2 ** min(self.failures, 5))
        if self.future is None and now >= self.next_refresh:
            self.next_refresh = now + INTERVAL
            self.future = self.executor.submit(self.rpc.fetch)
        if self.last_success is not None and now - self.last_success > 6:
            self.cached = replace(self.cached, stale=True)
        return self.cached

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.rpc.close()


class TokenRates:
    """Sample cumulative usage, excluding historical startup/replay increments."""
    def __init__(self):
        self.samples = {}
        self.rates = (UNKNOWN_U32, UNKNOWN_U32)
        self.next_sample = 0.0
        self.by_session = {}

    def update(self, sessions, now):
        if now < self.next_sample:
            return self.rates
        self.next_sample = now + INTERVAL
        live, rates, by_session = {}, [0.0, 0.0], {}
        known = False
        for session in sessions:
            counts = (session.input_tokens, session.output_tokens)
            if UNKNOWN_U64 in counts:
                continue
            known = True
            previous = self.samples.get(session.id)
            sample = (counts, now)
            session_rates = [0.0, 0.0]
            if previous:
                elapsed = max(INTERVAL, now - previous[1])
                for index in (0, 1):
                    session_rates[index] = max(0, counts[index] - previous[0][index]) / elapsed
                    rates[index] += session_rates[index]
            by_session[session.id] = tuple(min(UNKNOWN_U32 - 1, round(rate * 10))
                                           for rate in session_rates)
            live[session.id] = sample
        self.samples = live
        self.by_session = by_session
        self.rates = tuple(min(UNKNOWN_U32 - 1, round(rate * 10)) for rate in rates) \
            if known else (UNKNOWN_U32, UNKNOWN_U32)
        return self.rates


class UsageRouter:
    def __init__(self, codex_dir):
        self.codex_dir = Path(codex_dir)
        self.official = OfficialReader(codex_dir)
        self.morecode = MoreCodeReader()
        self.rates = TokenRates()
        self.config_stamp = None
        self.default_source = "official"

    def source_for(self, session=None):
        path = self.codex_dir / "config.toml"
        try:
            stamp = path.stat().st_mtime_ns
            if stamp != self.config_stamp:
                config = tomllib.loads(path.read_text())
                provider = config.get("model_provider", "openai")
                self.default_source = "official" if provider == "openai" else "morecode"
                self.config_stamp = stamp
        except (OSError, ValueError):
            pass
        provider = getattr(session, "model_provider", "")
        return ("official" if provider == "openai" else "morecode") if provider else self.default_source

    def snapshot(self, mode, selected, sessions, now):
        source = self.source_for(selected) if mode == "auto" else mode
        # Account selection affects billing only. Live speed follows the visible
        # conversation, including when inspecting a different account's quota.
        self.rates.update(sessions, now)
        rates = self.rates.by_session.get(getattr(selected, "id", ""),
                                         (UNKNOWN_U32, UNKNOWN_U32))
        if source == "morecode":
            usage = self.morecode.snapshot(now)
        else:
            usage = self.official.snapshot(now)
        return replace(usage, input_tps_x10=rates[0], output_tps_x10=rates[1])

    def close(self):
        self.official.close()
        self.morecode.close()
