from __future__ import annotations

from pathlib import Path

import pytest

import voicepipe.audio_feedback as af


# ---------- event_for_trigger_payload ----------


def test_event_for_trigger_payload_returns_none_for_non_dict() -> None:
    assert af.event_for_trigger_payload(None) is None
    assert af.event_for_trigger_payload("nope") is None
    assert af.event_for_trigger_payload(42) is None


def test_event_for_trigger_payload_returns_error_when_ok_false() -> None:
    payload = {"ok": False, "trigger": "zwingli", "error": "boom"}
    assert af.event_for_trigger_payload(payload) == "error"


def test_event_for_trigger_payload_returns_pending_when_handler_meta_pending() -> None:
    payload = {
        "ok": True,
        "trigger": "zwingli",
        "meta": {"handler_meta": {"pending": True}},
    }
    assert af.event_for_trigger_payload(payload) == "pending"


def test_event_for_trigger_payload_defaults_to_success() -> None:
    payload = {"ok": True, "trigger": "zwingli", "meta": {"verb": "subprocess"}}
    assert af.event_for_trigger_payload(payload) == "success"


# ---------- play() gating ----------


def test_play_is_no_op_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_AUDIO_FEEDBACK", raising=False)
    calls: list = []
    monkeypatch.setattr(af.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    af.play("success")
    assert calls == []


def test_play_is_no_op_for_unknown_event(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    calls: list = []
    monkeypatch.setattr(af.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    af.play("not_a_real_event")
    assert calls == []


def test_play_skips_when_sound_path_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    monkeypatch.setenv(
        "VOICEPIPE_AUDIO_FEEDBACK_SUCCESS", str(tmp_path / "does-not-exist.wav")
    )
    calls: list = []
    monkeypatch.setattr(af.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    af.play("success")
    assert calls == []


def test_play_swallows_popen_failures(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    sound = tmp_path / "ding.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK_SUCCESS", str(sound))
    monkeypatch.setattr(af, "_find_player", lambda: "/usr/bin/paplay")

    def _explode(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(af.subprocess, "Popen", _explode)
    # Must not raise.
    af.play("success")


# ---------- play() invocation ----------


def test_play_uses_override_sound_with_first_available_player(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    sound = tmp_path / "tone.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK_SUCCESS", str(sound))
    monkeypatch.setattr(af, "_find_player", lambda: "/usr/bin/paplay")
    monkeypatch.setattr(af, "is_windows", lambda: False)

    captured: list = []

    def _fake_popen(argv, **kwargs):
        captured.append((argv, kwargs))

        class _P:
            pass

        return _P()

    monkeypatch.setattr(af.subprocess, "Popen", _fake_popen)

    af.play("success")

    assert len(captured) == 1
    argv, kwargs = captured[0]
    assert argv == ["/usr/bin/paplay", str(sound)]
    assert kwargs["stdout"] == af.subprocess.DEVNULL
    assert kwargs["stderr"] == af.subprocess.DEVNULL
    assert kwargs["stdin"] == af.subprocess.DEVNULL


def test_play_uses_quiet_flags_for_ffplay(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    sound = tmp_path / "tone.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK_SUCCESS", str(sound))
    monkeypatch.setattr(af, "_find_player", lambda: "/usr/bin/ffplay")
    monkeypatch.setattr(af, "is_windows", lambda: False)

    captured: list = []
    monkeypatch.setattr(
        af.subprocess, "Popen", lambda argv, **k: captured.append(argv) or None
    )

    af.play("success")
    assert captured == [
        [
            "/usr/bin/ffplay",
            "-nodisp",
            "-autoexit",
            "-loglevel",
            "quiet",
            str(sound),
        ]
    ]


def test_play_skips_when_no_player_available(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", "1")
    sound = tmp_path / "tone.wav"
    sound.write_bytes(b"RIFF")
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK_SUCCESS", str(sound))
    monkeypatch.setattr(af, "_find_player", lambda: None)
    monkeypatch.setattr(af, "is_windows", lambda: False)

    captured: list = []
    monkeypatch.setattr(
        af.subprocess, "Popen", lambda *a, **k: captured.append((a, k))
    )

    af.play("success")
    assert captured == []


# ---------- env-var truthy parsing ----------


@pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", "y"])
def test_enabled_truthy_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", value)
    assert af._enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "garbage"])
def test_enabled_falsey_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("VOICEPIPE_AUDIO_FEEDBACK", value)
    assert af._enabled() is False
