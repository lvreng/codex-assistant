from concurrent.futures import Future
from dataclasses import replace

import pytest

from codex_state import EventState, Session
from desktop_ipc import DesktopClient, DesktopHub
from official_usage import account_usage, AccountRPC, OfficialReader, TokenRates, UsageRouter
from settings import Settings
from usage import Usage, UNKNOWN_U32


def test_week_is_selected_by_duration_not_slot_and_only_from_codex_bucket():
    limits = {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": 24.5, "windowDurationMins": 10080, "resetsAt": 1790707200},
        "secondary": {"usedPercent": 80, "windowDurationMins": 300}},
        "review": {"primary": {"usedPercent": 99, "windowDurationMins": 10080}}}}
    usage = account_usage({"type": "chatgpt", "planType": "pro"}, limits)
    assert usage.week_remaining_x10 == 755
    assert usage.plan_name == "Pro"
    assert usage.week_reset
    assert usage.balance_quota == UNKNOWN_U32


@pytest.mark.parametrize("used,expected", [(0, 1000), (100, 0), (110, 0), (-2, 1000),
                                          (None, 65535), (float("nan"), 65535)])
def test_percent_clamp_and_unknown(used, expected):
    usage = account_usage({"type": "chatgpt"}, {"rateLimits": {
        "secondary": {"usedPercent": used, "windowDurationMins": 10080}}})
    assert usage.week_remaining_x10 == expected


def test_api_key_does_not_claim_chatgpt_subscription_or_weekly_allowance():
    usage = account_usage({"type": "apiKey"}, {"rateLimits": {
        "secondary": {"usedPercent": 10, "windowDurationMins": 10080}}})
    assert usage.account_kind == 2
    assert usage.week_remaining_x10 == 65535
    assert usage.plan_name == ""


def test_no_week_and_credit_expiration_are_not_membership_expiration():
    usage = account_usage({"type": "chatgpt", "planType": "plus"}, {
        "rateLimits": {"primary": {"usedPercent": 20, "windowDurationMins": 300}},
        "rateLimitResetCredits": {"credits": [{"expiresAt": 1790707200}]}})
    assert usage.week_remaining_x10 == 65535
    assert usage.week_reset == ""


def test_two_second_single_flight_and_error_backoff(tmp_path):
    class Executor:
        def __init__(self):
            self.futures = []

        def submit(self, fn):
            future = Future()
            self.futures.append(future)
            return future

        def shutdown(self, **kwargs):
            pass

    reader = OfficialReader(tmp_path)
    reader.executor.shutdown()
    reader.executor = Executor()
    reader.snapshot(0)
    reader.snapshot(1)
    assert len(reader.executor.futures) == 1
    reader.executor.futures[0].set_result(account_usage({"type": "chatgpt"}, {}))
    reader.snapshot(1.5)
    assert len(reader.executor.futures) == 1
    reader.snapshot(2)
    assert len(reader.executor.futures) == 2
    reader.snapshot(10)  # Hung request cannot spawn duplicates.
    assert len(reader.executor.futures) == 2
    reader.executor.futures[1].set_exception(RuntimeError("429"))
    assert reader.snapshot(10).stale
    reader.snapshot(13.9)
    assert len(reader.executor.futures) == 2
    reader.snapshot(14)
    assert len(reader.executor.futures) == 3
    reader.close()


def test_actual_network_requests_remain_two_seconds_apart_after_slow_startup(tmp_path, monkeypatch):
    import official_usage
    clock = [0.0]
    calls = []
    monkeypatch.setattr(official_usage.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(official_usage.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    rpc = AccountRPC(tmp_path)
    rpc.process = type("Process", (), {"poll": lambda self: None})()

    def call(method, params):
        if method == "account/read":
            clock[0] += .1
            return {"account": {"type": "chatgpt"}}
        calls.append(clock[0])
        return {}

    monkeypatch.setattr(rpc, "call", call)
    rpc.fetch()
    rpc.fetch()
    rpc.fetch()
    assert calls == pytest.approx([.1, 2.1, 4.1])


def test_token_rates_seed_history_and_count_input_output_deltas_every_two_seconds():
    sample = Session("a", "", "", "thinking", input_tokens=100000, output_tokens=10000)
    rates = TokenRates()
    assert rates.update([sample], 0) == (0, 0)
    following = replace(sample, input_tokens=100200, output_tokens=10040)
    assert rates.update([following], 1) == (0, 0)
    assert rates.update([following], 2) == (1000, 200)
    assert rates.update([following], 4) == (0, 0)
    assert rates.update([replace(sample, input_tokens=1, output_tokens=1)], 6) == (0, 0)
    assert rates.update([], 8) == (UNKNOWN_U32, UNKNOWN_U32)


def test_token_event_and_provider_survive_next_turn_reset():
    state = EventState()
    state.feed({"type": "turn_context", "payload": {"model_provider": "openai"}})
    state.feed({"type": "event_msg", "payload": {"type": "token_count", "info": {
        "total_token_usage": {"total_tokens": 123, "input_tokens": 100, "output_tokens": 23}}}})
    state.reset("next-turn")
    assert (state.input_tokens, state.output_tokens, state.model_provider) == (100, 23, "openai")


def test_auto_source_uses_session_provider_before_current_default(tmp_path):
    (tmp_path / "config.toml").write_text('model_provider = "custom"\n')
    router = UsageRouter(tmp_path)
    try:
        assert router.source_for() == "morecode"
        assert router.source_for(Session("a", "", "", "done", model_provider="openai")) == "official"
        assert router.source_for(Session("b", "", "", "done", model_provider="custom")) == "morecode"
    finally:
        router.close()


def test_tokens_follow_visible_conversation_even_when_billing_source_is_different(tmp_path):
    router = UsageRouter(tmp_path)
    router.official.snapshot = lambda now: Usage(api_source="official", account_kind=1)
    router.morecode.snapshot = lambda now: Usage(balance_quota=123, api_source="morecode")
    first = Session("a", "", "", "thinking", model_provider="custom",
                    input_tokens=100, output_tokens=10)
    second = Session("b", "", "", "done", model_provider="openai",
                     input_tokens=800, output_tokens=80)
    try:
        initial = router.snapshot("official", first, [first, second], 0)
        assert initial.input_tps_x10 == initial.output_tps_x10 == 0
        first = replace(first, input_tokens=200, output_tokens=30)
        usage = router.snapshot("official", first, [first, second], 2)
        assert usage.api_source == "official"
        assert (usage.input_tps_x10, usage.output_tps_x10) == (500, 100)
        usage = router.snapshot("morecode", first, [first, second], 2.1)
        assert usage.balance_quota == 123
        assert (usage.input_tps_x10, usage.output_tps_x10) == (500, 100)
        usage = router.snapshot("official", second, [first, second], 2.2)
        assert (usage.input_tps_x10, usage.output_tps_x10) == (0, 0)
    finally:
        router.close()


def test_source_selection_ipc_requires_subscription_and_persists_settings(tmp_path):
    directory = tmp_path / "ipc"
    hub = DesktopHub(directory)
    client = DesktopClient(directory)
    try:
        client.set_api_mode("official")
        hub.poll()
        assert hub.api_mode_request is None
        client.subscribe()
        hub.poll()
        assert client.set_api_mode("morecode")
        hub.poll()
        assert hub.api_mode_request == "morecode"
        client.set_membership_expiry("2026-10-22T23:59")
        hub.poll()
        assert hub.membership_expiry_request == "2026-10-22T23:59"
        client.set_membership_expiry("")
        hub.poll()
        assert hub.membership_expiry_request == ""
        path = tmp_path / "settings.json"
        settings = Settings(path)
        settings.set_api_mode(hub.api_mode_request)
        assert Settings(path).poll()["api_mode"] == "morecode"
        with pytest.raises(ValueError):
            settings.set_api_mode("unknown")
    finally:
        client.close()
        hub.close()
