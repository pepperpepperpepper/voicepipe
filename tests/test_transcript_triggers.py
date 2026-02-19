from __future__ import annotations

import voicepipe.transcript_triggers as tt


def test_match_transcript_trigger_prefix_variants() -> None:
    triggers = {"zwingly": "zwingli"}

    m = tt.match_transcript_trigger("zwingly do it", triggers=triggers)
    assert m is not None
    assert m.trigger == "zwingly"
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("Zwingly, do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly: do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly; do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly. do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"


def test_match_transcript_trigger_requires_boundary() -> None:
    triggers = {"zwingly": "zwingli"}
    assert tt.match_transcript_trigger("zwinglyx do it", triggers=triggers) is None


def test_apply_transcript_triggers_no_match_returns_original() -> None:
    out, meta = tt.apply_transcript_triggers("hello world", triggers={"zwingly": "zwingli"})
    assert out == "hello world"
    assert meta is None


def test_apply_transcript_triggers_invokes_handler(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_handler(prompt: str):
        calls.append(prompt)
        return "processed", {"provider": "fake"}

    monkeypatch.setitem(tt._ACTIONS, "zwingli", _fake_handler)

    out, meta = tt.apply_transcript_triggers("zwingly do it", triggers={"zwingly": "zwingli"})
    assert calls == ["do it"]
    assert out == "processed"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["trigger"] == "zwingly"
    assert meta["action"] == "zwingli"
    assert meta["meta"] == {"provider": "fake"}


def test_apply_transcript_triggers_unknown_action_falls_back() -> None:
    out, meta = tt.apply_transcript_triggers("zwingly do it", triggers={"zwingly": "nope"})
    assert out == "do it"
    assert meta is not None
    assert meta["ok"] is False
    assert meta["error"]

