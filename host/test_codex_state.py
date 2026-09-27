import time

from codex_state import Chooser, EventState, Session, classify, short_name


def sessions():
    return [
        Session("a", "", "甲对话", "working"),
        Session("b", "", "乙对话", "thinking"),
        Session("c", "", "丙对话", "done"),
    ]


def test_chooser_rotates_only_active_sessions_every_five_seconds():
    chooser = Chooser()
    assert chooser.choose(sessions(), 0).id == "a"
    assert chooser.choose(sessions(), 4.9).id == "a"
    assert chooser.choose(sessions(), 5.0).id == "b"
    assert chooser.choose(sessions(), 10.0).id == "a"


def test_single_active_session_does_not_rotate():
    chooser = Chooser()
    values = [
        Session("a", "", "甲对话", "working"),
        Session("b", "", "乙对话", "done"),
        Session("c", "", "丙对话", "waiting"),
    ]
    assert chooser.choose(values, 0).id == "a"
    assert chooser.choose(values, 30).id == "a"


def test_manual_step_can_browse_all_then_returns_to_active_session():
    chooser = Chooser(interval=10)
    values = [
        Session("a", "", "甲对话", "working"),
        Session("b", "", "乙对话", "done"),
        Session("c", "", "丙对话", "done"),
    ]
    assert chooser.choose(values, 0).id == "a"
    assert chooser.step(values, 1, 1).id == "b"
    assert chooser.choose(values, 3.9).id == "b"
    assert chooser.choose(values, 4.0).id == "a"
    assert chooser.choose(values, 30).id == "a"


def test_all_finished_sessions_rotate_normally():
    chooser = Chooser(mode="all")
    values = [
        Session("a", "", "甲对话", "done"),
        Session("b", "", "乙对话", "done"),
        Session("c", "", "丙对话", "paused"),
    ]
    assert chooser.choose(values, 0).id == "a"
    assert chooser.choose(values, 5.0).id == "b"
    assert chooser.choose(values, 10.0).id == "c"


def test_rotation_off_keeps_current_until_it_disappears():
    chooser = Chooser(mode="off")
    values = sessions()
    assert chooser.choose(values, 0).id == "a"
    assert chooser.choose(values, 500).id == "a"
    assert chooser.choose(values[1:], 501).id == "b"


def test_active_mode_does_not_rotate_finished_conversations():
    chooser = Chooser(mode="active")
    values = [Session("a", "", "甲对话", "done"), Session("b", "", "乙对话", "paused")]
    assert chooser.choose(values, 0).id == "a"
    assert chooser.choose(values, 500).id == "a"
    values.append(Session("c", "", "丙对话", "working"))
    assert chooser.choose(values, 501).id == "c"


def test_all_mode_rotates_active_and_finished_together():
    chooser = Chooser(mode="all")
    assert chooser.choose(sessions(), 0).id == "a"
    assert chooser.choose(sessions(), 5).id == "b"
    assert chooser.choose(sessions(), 10).id == "c"


def test_mode_change_restarts_five_second_interval():
    chooser = Chooser(mode="off")
    assert chooser.choose(sessions(), 0).id == "a"
    assert chooser.configure("all", 100)
    assert chooser.choose(sessions(), 104.9).id == "a"
    assert chooser.choose(sessions(), 105).id == "b"
    assert not chooser.configure("all", 106)


def test_short_name_keeps_at_most_twelve_characters():
    assert short_name("一二三四五六七八九十甲乙丙丁", "", "thread", {}) == \
        "一二三四五六七八九十甲乙"


def event(event_type, payload_type, **payload):
    return {"type": event_type, "payload": {"type": payload_type, **payload}}


def in_progress(turn_id="t1"):
    return {"turn_id": turn_id, "status": "inProgress"}


def test_function_call_is_working_even_without_item_started():
    events = EventState("t1")
    events.feed(event("response_item", "function_call", name="exec_command", call_id="1"))
    assert classify(in_progress(), events, []) == "working"


def test_web_run_is_searching_and_survives_fast_output():
    events = EventState("t1")
    events.feed(event("response_item", "function_call", name="web.run", call_id="1"))
    events.feed(event("response_item", "function_call_output", call_id="1"))
    assert classify(in_progress(), events, []) == "searching"


def test_waiting_tool_is_waiting():
    events = EventState("t1")
    events.feed(event("response_item", "custom_tool_call", name="request_user_input", call_id="1"))
    assert classify(in_progress(), events, []) == "waiting"


def test_uppercase_item_types_are_supported():
    events = EventState("t1")
    events.feed(event("event_msg", "item_started", item={"type": "CommandExecution"}))
    assert classify(in_progress(), events, []) == "working"


def test_failed_and_interrupted_turns_are_distinct():
    events = EventState("t1")
    assert classify({"turn_id": "t1", "status": "failed"}, events, []) == "error"
    assert classify({"turn_id": "t1", "status": "interrupted"}, events, []) == "paused"


def test_task_complete_is_done():
    events = EventState("t1")
    events.feed(event("event_msg", "task_complete", turn_id="t1"))
    assert classify(in_progress(), events, []) == "done"


def test_tool_hold_expires():
    events = EventState("t1")
    events.feed(event("response_item", "function_call", name="exec_command", call_id="1"))
    events.feed(event("response_item", "function_call_output", call_id="1"))
    assert events.state() == "working"
    events.last_tool_at = time.monotonic() - 1.0
    assert events.state() == "thinking"


def test_session_model_defaults_to_empty_string():
    assert Session("a", "", "a", "idle").model == ""


def test_turn_context_model_is_bounded_and_survives_task_started():
    events = EventState("t1")
    model = "model-name-" + ("x" * 200)
    events.feed({"type": "turn_context", "payload": {"model": model}})
    assert len(events.model) == 160
    events.feed(event("event_msg", "task_started", turn_id="t2"))
    assert events.model == model[:160]


def test_tail_reset_keeps_row_model_fallback(tmp_path):
    from codex_state import TailReader

    path = tmp_path / "rollout.jsonl"
    path.write_text("{}\n")
    tail = TailReader(path, "t1", "db-model")
    tail.events.feed({"type": "turn_context", "payload": {"model": "event-model"}})
    tail.events.feed(event("event_msg", "task_started", turn_id="t2"))
    tail.offset = path.stat().st_size
    path.write_text("")
    tail.poll()
    assert tail.events.model == "db-model"


def test_model_updates_from_database_and_ignores_message_payloads():
    events = EventState("t1", "gpt-5.6-sol")
    events.feed({"type": "turn_context", "payload": {"model": "gpt-5.6-terra"}})
    events.feed(event("response_item", "message", model="not-a-context"))
    assert events.model == "gpt-5.6-terra"
    events.set_fallback_model("gpt-6-astra")
    assert events.model == "gpt-6-astra"


def test_detector_optional_database_model_column(tmp_path):
    import sqlite3
    from codex_state import Detector

    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n")
    with sqlite3.connect(tmp_path / "state_1.sqlite") as db:
        db.execute("CREATE TABLE threads (id, rollout_path, name, title, cwd, source, "
                   "agent_path, updated_at, archived)")
        db.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("a", str(rollout), "one", "one", str(tmp_path), "", "", 10, 0))
    with sqlite3.connect(tmp_path / "thread_history_1.sqlite") as db:
        db.execute("CREATE TABLE thread_turns "
                   "(thread_id, turn_id, status, started_at, completed_at, rollout_ordinal)")
    detector = Detector(tmp_path, lock_provider=lambda _: {"a": 123})
    assert detector.snapshot()[0].model == ""
    with sqlite3.connect(tmp_path / "state_1.sqlite") as db:
        db.execute("ALTER TABLE threads ADD COLUMN model TEXT")
        db.execute("UPDATE threads SET model='gpt-5.6-sol'")
    assert detector.snapshot()[0].model == "gpt-5.6-sol"
    with sqlite3.connect(tmp_path / "state_1.sqlite") as db:
        db.execute("UPDATE threads SET model='gpt-6-astra'")
    assert detector.snapshot()[0].model == "gpt-6-astra"
