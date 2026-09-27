import usage as usage_module
from usage import MoreCodeReader, UNKNOWN_U32, UNKNOWN_U64, Usage


def test_unknown_usage_defaults():
    usage = Usage()
    assert usage.balance_quota == UNKNOWN_U32
    assert usage.month_tokens == UNKNOWN_U64
    assert usage.today_prompt_tokens == UNKNOWN_U64
    assert not usage.valid


def test_morecode_payload_is_aggregated_without_unit_conversion():
    reader = MoreCodeReader()
    reader._session_cookie = lambda: "session"
    reader._candidate_user_ids = lambda: [42]

    def fake_json(path, user_id=None, cookie=None):
        if path == "/api/user/self":
            return {
                "quota": 17_122_315,
                "used_quota": 102_887_685,
                "request_count": 2895,
            }
        if path == "/api/status":
            return {"quota_per_unit": 500_000}
        return {
            "total": 1,
            "items": [{
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "quota": 1_000_000,
                "model_name": "alpha",
            }],
        }

    reader._json = fake_json
    result = reader._fetch(1_790_000_000)
    assert result.balance_quota == 17_122_315
    assert result.spent_quota == 102_887_685
    assert result.request_count == 2895
    assert result.month_request_count == 1
    assert result.month_tokens == 30
    assert result.month_quota == 1_000_000
    assert result.today_request_count == 1
    assert result.today_prompt_tokens == 10
    assert result.today_completion_tokens == 20
    assert result.models[0]["name"] == "alpha"
    assert result.quota_per_unit == 500_000
    assert not result.stale


def test_today_aggregation_sorts_models_and_limits_to_four():
    reader = MoreCodeReader()
    reader._session_cookie = lambda: "session"
    reader._candidate_user_ids = lambda: [42]
    items = [{"model_name": name, "quota": quota, "prompt_tokens": 1,
              "completion_tokens": 2} for name, quota in
             [("a", 1), ("b", 5), ("c", 3), ("d", 4), ("e", 9)]]

    def fake_json(path, user_id=None, cookie=None):
        if path == "/api/user/self":
            return {"quota": 1, "used_quota": 2, "request_count": 3}
        if path == "/api/status":
            return {"quota_per_unit": 500_000}
        return {"total": len(items), "items": items}

    reader._json = fake_json
    result = reader._fetch(1_800_000_000)
    assert [model["name"] for model in result.models] == ["e", "b", "d", "c"]
    assert result.today_request_count == 5


def test_light_rate_uses_new_logs_and_ema_decay():
    reader = MoreCodeReader()
    reader._light_seen = {"id:1"}
    reader._light_seeded = True
    reader._light_last_epoch = 100.0
    reader._fetch_light = lambda now: (now, {"quota": 1, "used_quota": 2, "request_count": 3},
                                       [{"id": 1, "prompt_tokens": 10, "completion_tokens": 20},
                                        {"id": 2, "prompt_tokens": 30, "completion_tokens": 40}])
    reader._apply_light(reader._fetch_light(110.0))
    assert reader.cached.input_tps_x10 == 15
    assert reader.cached.output_tps_x10 == 20
    reader._apply_light((120.0, {"quota": 1, "used_quota": 2, "request_count": 3},
                         [{"id": 1, "prompt_tokens": 10, "completion_tokens": 20},
                          {"id": 2, "prompt_tokens": 30, "completion_tokens": 40}]))
    assert reader.cached.input_tps_x10 == 7
    assert reader.cached.output_tps_x10 == 10


def test_first_light_refresh_only_seeds_history():
    reader = MoreCodeReader()
    reader._apply_light((
        110.0,
        {"quota": 1, "used_quota": 2, "request_count": 3},
        [{"id": 1, "prompt_tokens": 30_000, "completion_tokens": 40_000}],
    ))
    assert reader.cached.input_tps_x10 == 0
    assert reader.cached.output_tps_x10 == 0


def test_light_rate_detects_new_request_when_page_row_id_is_reused():
    reader = MoreCodeReader()
    reader._apply_light((
        100.0,
        None,
        [{"id": 1, "request_id": "old", "prompt_tokens": 10,
          "completion_tokens": 20}],
    ))
    reader._apply_light((
        102.0,
        None,
        [{"id": 1, "request_id": "new", "prompt_tokens": 100,
          "completion_tokens": 40}],
    ))
    assert reader.cached.input_tps_x10 == 250
    assert reader.cached.output_tps_x10 == 100


def test_snapshot_failure_keeps_last_cache_and_marks_stale():
    reader = MoreCodeReader()
    reader.cached = Usage(balance_quota=123, stale=False)
    reader._fetch = lambda now: (_ for _ in ()).throw(RuntimeError("offline"))
    reader.snapshot(now_monotonic=0, now_epoch=1)
    reader._future.done()
    result = reader.snapshot(now_monotonic=0, now_epoch=1)
    assert result.balance_quota == 123
    assert result.stale


def test_json_rate_limits_consecutive_requests_across_paths(monkeypatch):
    reader = MoreCodeReader()
    clock = [100.0]
    sleeps = []
    paths = []

    def fake_monotonic():
        return clock[0]

    def fake_sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_urlopen(request, timeout):
        paths.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(usage_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(usage_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(usage_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        usage_module.json,
        "load",
        lambda response: {"success": True, "data": response},
    )

    reader._json("/api/user/self")
    reader._json("/api/status")

    assert paths == [
        "https://api.morecode.top/api/user/self",
        "https://api.morecode.top/api/status",
    ]
    assert sleeps == [2.0]
