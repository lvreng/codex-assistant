"""Read MoreCode account usage through the browser's authenticated session."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request
import threading


UNKNOWN_U32 = 0xFFFFFFFF
UNKNOWN_U64 = 0xFFFFFFFFFFFFFFFF


@dataclass(frozen=True)
class Usage:
    balance_quota: int = UNKNOWN_U32
    spent_quota: int = UNKNOWN_U32
    request_count: int = UNKNOWN_U32
    month_request_count: int = UNKNOWN_U32
    month_quota: int = UNKNOWN_U32
    month_tokens: int = UNKNOWN_U64
    today_request_count: int = UNKNOWN_U32
    today_quota: int = UNKNOWN_U32
    today_prompt_tokens: int = UNKNOWN_U64
    today_completion_tokens: int = UNKNOWN_U64
    input_tps_x10: int = UNKNOWN_U32
    output_tps_x10: int = UNKNOWN_U32
    quota_per_unit: int = 500_000
    models: list[dict] = field(default_factory=list)
    stale: bool = True
    api_source: str = "morecode"
    account_kind: int = 0
    week_remaining_x10: int = 65535
    week_reset: str = ""
    plan_name: str = ""

    @property
    def valid(self) -> bool:
        return self.balance_quota != UNKNOWN_U32


class MoreCodeReader:
    """Fetch and cache account totals without persisting browser credentials."""

    def __init__(
        self,
        refresh_seconds: float = 60.0,
        origin: str = "https://api.morecode.top",
        edge_dir: Path | None = None,
    ):
        self.refresh_seconds = refresh_seconds
        self.origin = origin.rstrip("/")
        self.edge_dir = edge_dir or Path.home() / ".config/microsoft-edge/Default"
        self.next_refresh = 0.0
        self.cached = Usage()
        self.error: str | None = None
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future = None
        self._future_kind: str | None = None
        self.next_light_refresh = 0.0
        self._light_seen: set[str] = set()
        self._light_seeded = False
        self._light_last_epoch: float | None = None
        self._input_ema = 0.0
        self._output_ema = 0.0
        self._user_id: int | None = None
        self._request_lock = threading.Lock()
        self._next_request_at = 0.0
        self.request_interval_seconds = 2.0

    def _candidate_user_ids(self) -> list[int]:
        """Extract non-secret user IDs from Chromium Local Storage records."""
        leveldb = self.edge_dir / "Local Storage/leveldb"
        files = sorted(
            (*leveldb.glob("*.log"), *leveldb.glob("*.ldb")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        candidates: list[int] = []
        for path in files:
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            marker = b'\x01{"display_name":'
            start = 0
            while True:
                start = raw.find(marker, start)
                if start < 0:
                    break
                end = raw.find(b"}", start, start + 2048)
                if end > start:
                    try:
                        record = json.loads(raw[start + 1 : end + 1])
                        required = {"display_name", "group", "id", "role", "status", "username"}
                        if required <= record.keys():
                            value = int(record["id"])
                            if value not in candidates:
                                candidates.append(value)
                    except (UnicodeError, ValueError, TypeError):
                        pass
                start += len(marker)
            for match in re.finditer(
                rb'"group":"[^"]+","id":(\d+),"role":\d+,"status":\d+,"username"',
                raw,
            ):
                value = int(match.group(1))
                if value not in candidates:
                    candidates.append(value)
        return candidates

    @staticmethod
    def _session_cookie() -> str:
        try:
            import browser_cookie3
        except ImportError as exc:
            raise RuntimeError("缺少 browser-cookie3，请重新运行 host 安装") from exc
        jar = browser_cookie3.edge(domain_name="api.morecode.top")
        for cookie in jar:
            if cookie.name == "session" and cookie.value:
                return cookie.value
        raise RuntimeError("Microsoft Edge 中没有有效的 MoreCode 登录会话")

    def _json(self, path: str, user_id: int | None = None, cookie: str | None = None):
        with self._request_lock:
            delay = self._next_request_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._next_request_at = time.monotonic() + self.request_interval_seconds
        headers = {"Accept": "application/json", "User-Agent": "codex-pet/2.0"}
        if user_id is not None:
            headers["New-API-User"] = str(user_id)
        if cookie:
            headers["Cookie"] = f"session={cookie}"
        request = urllib.request.Request(self.origin + path, headers=headers)
        with urllib.request.urlopen(request, timeout=8) as response:
            envelope = json.load(response)
        if not envelope.get("success"):
            raise RuntimeError(str(envelope.get("message") or "MoreCode API 请求失败"))
        return envelope.get("data")

    def _authenticated(self, path: str, user_ids: list[int], cookie: str):
        last_error: Exception | None = None
        for user_id in user_ids:
            try:
                return user_id, self._json(path, user_id, cookie)
            except Exception as exc:
                last_error = exc
        raise last_error or RuntimeError("无法识别 MoreCode 登录用户")

    @staticmethod
    def _today_range(now_epoch: float) -> tuple[int, int]:
        now = datetime.fromtimestamp(now_epoch).astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int(now_epoch)

    def _log_path(self, page: int, start: int, end: int) -> str:
        query = urllib.parse.urlencode(
            {
                "p": page,
                "page_size": 100,
                "type": 0,
                "token_name": "",
                "model_name": "",
                "start_timestamp": start,
                "end_timestamp": end,
                "group": "",
                "request_id": "",
            }
        )
        return f"/api/log/self/?{query}"

    def _fetch(self, now_epoch: float) -> Usage:
        cookie = self._session_cookie()
        user_id, account = self._authenticated(
            "/api/user/self", self._candidate_user_ids(), cookie
        )
        self._user_id = user_id
        status = self._json("/api/status")
        start, end = self._today_range(now_epoch)
        first = self._json(self._log_path(1, start, end), user_id, cookie)
        total = max(0, int(first.get("total", 0)))
        pages = (total + 99) // 100
        items = list(first.get("items") or [])

        def fetch_page(page: int):
            data = self._json(self._log_path(page, start, end), user_id, cookie)
            return data.get("items") or []

        for page in range(2, pages + 1):
            items.extend(fetch_page(page))

        model_totals = {}
        for item in items:
            name = str(item.get("model_name") or "unknown")
            model = model_totals.setdefault(name, {"name": name, "quota": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "request_count": 0})
            model["quota"] += max(0, int(item.get("quota") or 0))
            model["prompt_tokens"] += max(0, int(item.get("prompt_tokens") or 0))
            model["completion_tokens"] += max(0, int(item.get("completion_tokens") or 0))
            model["request_count"] += 1
        models = sorted(model_totals.values(), key=lambda model: model["quota"], reverse=True)[:4]
        today_prompt = sum(model["prompt_tokens"] for model in model_totals.values())
        today_completion = sum(model["completion_tokens"] for model in model_totals.values())
        today_quota = sum(model["quota"] for model in model_totals.values())
        return Usage(
            balance_quota=max(0, min(UNKNOWN_U32 - 1, int(account["quota"]))),
            spent_quota=max(0, min(UNKNOWN_U32 - 1, int(account["used_quota"]))),
            request_count=max(0, min(UNKNOWN_U32 - 1, int(account["request_count"]))),
            month_request_count=min(UNKNOWN_U32 - 1, total),
            month_quota=min(UNKNOWN_U32 - 1, today_quota),
            month_tokens=min(UNKNOWN_U64 - 1, today_prompt + today_completion),
            today_request_count=min(UNKNOWN_U32 - 1, total),
            today_quota=min(UNKNOWN_U32 - 1, today_quota),
            today_prompt_tokens=min(UNKNOWN_U64 - 1, today_prompt),
            today_completion_tokens=min(UNKNOWN_U64 - 1, today_completion),
            quota_per_unit=max(
                1, min(UNKNOWN_U32 - 1, int(status.get("quota_per_unit", 500_000)))
            ),
            models=models,
            stale=False,
        )

    def _fetch_light(self, now_epoch: float):
        cookie = self._session_cookie()
        user_id = self._user_id
        if user_id is None:
            user_id, account = self._authenticated(
                "/api/user/self", self._candidate_user_ids(), cookie
            )
            self._user_id = user_id
        else:
            account = None
        start, end = self._today_range(now_epoch)
        page = self._json(self._log_path(1, start, end), user_id, cookie)
        return now_epoch, account, list(page.get("items") or [])

    @staticmethod
    def _item_key(item: dict) -> str:
        # MoreCode's `id` is the row number in the current page and is reused.
        # `request_id` is stable across refreshes and uniquely identifies usage.
        for key in ("request_id", "id"):
            if item.get(key) is not None:
                return f"{key}:{item[key]}"
        identifying = {
            key: item.get(key)
            for key in (
                "created_at", "model_name", "prompt_tokens", "completion_tokens",
                "quota", "use_time",
            )
        }
        return json.dumps(identifying, sort_keys=True, separators=(",", ":"))

    def _apply_light(self, result) -> None:
        now_epoch, account, items = result
        current = {self._item_key(item) for item in items}
        new_items = (
            [item for item in items if self._item_key(item) not in self._light_seen]
            if self._light_seeded else []
        )
        if self._light_seeded and self._light_last_epoch is not None:
            elapsed = max(0.001, now_epoch - self._light_last_epoch)
            input_rate = sum(max(0, int(item.get("prompt_tokens") or 0)) for item in new_items) / elapsed
            output_rate = sum(max(0, int(item.get("completion_tokens") or 0)) for item in new_items) / elapsed
            alpha = 0.5
            self._input_ema = alpha * input_rate + (1 - alpha) * self._input_ema
            self._output_ema = alpha * output_rate + (1 - alpha) * self._output_ema
        self._light_seen = current
        self._light_seeded = True
        self._light_last_epoch = now_epoch
        account_values = {} if account is None else {
            "balance_quota": max(0, min(UNKNOWN_U32 - 1, int(account["quota"]))),
            "spent_quota": max(0, min(UNKNOWN_U32 - 1, int(account["used_quota"]))),
            "request_count": max(0, min(UNKNOWN_U32 - 1, int(account["request_count"]))),
        }
        self.cached = replace(self.cached,
            input_tps_x10=max(0, min(UNKNOWN_U32 - 1, int(self._input_ema * 10))),
            output_tps_x10=max(0, min(UNKNOWN_U32 - 1, int(self._output_ema * 10))),
            stale=False,
            **account_values)

    def snapshot(
        self,
        now_monotonic: float | None = None,
        now_epoch: float | None = None,
    ) -> Usage:
        now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
        with self._lock:
            if self._future is not None and self._future.done():
                try:
                    result = self._future.result()
                    if self._future_kind == "light":
                        self._apply_light(result)
                    else:
                        self.cached = self._merge_rates(result)
                    self.error = None
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    if self.cached.valid:
                        self.cached = replace(self.cached, stale=True)
                self._future = None
                self._future_kind = None
            if now_monotonic >= self.next_refresh and self._future is None:
                self.next_refresh = now_monotonic + self.refresh_seconds
                self.next_light_refresh = now_monotonic + self.request_interval_seconds
                fetch_epoch = time.time() if now_epoch is None else now_epoch
                self._future = self._executor.submit(self._fetch, fetch_epoch)
                self._future_kind = "full"
            elif now_monotonic >= self.next_light_refresh and self._future is None:
                self.next_light_refresh = now_monotonic + self.request_interval_seconds
                fetch_epoch = time.time() if now_epoch is None else now_epoch
                self._future = self._executor.submit(self._fetch_light, fetch_epoch)
                self._future_kind = "light"
            return self.cached

    def _merge_rates(self, usage: Usage) -> Usage:
        return replace(usage, input_tps_x10=self.cached.input_tps_x10,
                       output_tps_x10=self.cached.output_tps_x10)

    def close(self):
        self._executor.shutdown(wait=True, cancel_futures=True)
