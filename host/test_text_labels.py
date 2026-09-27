import text_labels
from codex_state import Session


def test_labels_render_current_and_abbreviated_next_session(monkeypatch):
    calls = []

    def fake_mask(text, height=24, size=18, align="center"):
        calls.append((text, height, align))
        return bytes(text_labels.WIDTH * height // 8)

    monkeypatch.setattr(text_labels, "mask", fake_mask)
    active = Session("a", "", "当前对话", "working")
    following = Session("b", "", "下一个很长", "thinking")

    result = text_labels.labels(active, [active, following])

    assert calls[:2] == [
        ("当前对话  1/2", 24, "left"),
        ("下一个…", 24, "left"),
    ]
    assert len(result) == 2240


def test_labels_limit_current_session_name_to_twelve_characters(monkeypatch):
    calls = []

    def fake_mask(text, height=24, size=18, align="center"):
        calls.append((text, size))
        return bytes(text_labels.WIDTH * height // 8)

    monkeypatch.setattr(text_labels, "mask", fake_mask)
    active = Session("a", "", "一二三四五六七八九十甲乙丙丁", "working")

    text_labels.labels(active, [active])

    assert calls[0] == ("一二三四五六七八九十甲乙  1/1", 15)
