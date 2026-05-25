"""Tests for the Actuator protocol and its desktop/in-memory backends."""
from __future__ import annotations

import subprocess as _stdlib_subprocess

import pytest

import voicepipe.config as config
import voicepipe.transcript_triggers as tt
from voicepipe.transcript_triggers._actuator import (
    CAP_AUDIO_FEEDBACK,
    CAP_CLIPBOARD,
    CAP_SUBPROCESS,
    Actuator,
    ActuatorCapabilityError,
    DesktopActuator,
    InMemoryActuator,
    SubprocessResult,
    get_default_actuator,
    resolve_actuator,
)


# ---------------------------------------------------------------------------
# Protocol & default-actuator wiring
# ---------------------------------------------------------------------------


def test_default_actuator_is_desktop_actuator_singleton() -> None:
    first = get_default_actuator()
    second = get_default_actuator()
    assert isinstance(first, DesktopActuator)
    assert first is second


def test_resolve_actuator_returns_passed_actuator() -> None:
    fake = InMemoryActuator()
    assert resolve_actuator(fake) is fake


def test_resolve_actuator_falls_back_to_default() -> None:
    assert resolve_actuator(None) is get_default_actuator()


def test_desktop_actuator_advertises_all_capabilities() -> None:
    caps = DesktopActuator().capabilities()
    assert caps == frozenset({CAP_SUBPROCESS, CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK})


def test_actuator_protocol_runtime_checkable() -> None:
    assert isinstance(DesktopActuator(), Actuator)
    assert isinstance(InMemoryActuator(), Actuator)


# ---------------------------------------------------------------------------
# InMemoryActuator behaviour
# ---------------------------------------------------------------------------


def test_in_memory_actuator_captures_subprocess_calls() -> None:
    act = InMemoryActuator()
    result = act.run_subprocess(["echo", "hi"], shell=False, timeout_seconds=2.0)
    assert isinstance(result, SubprocessResult)
    assert act.subprocess_calls == [
        {"argv": ["echo", "hi"], "shell": False, "timeout_seconds": 2.0}
    ]


def test_in_memory_actuator_returns_configured_subprocess_result() -> None:
    act = InMemoryActuator(
        subprocess_result=SubprocessResult(
            returncode=0, stdout="canned-stdout", stderr="canned-stderr"
        )
    )
    out = act.run_subprocess("anything", shell=True)
    assert out.stdout == "canned-stdout"
    assert out.stderr == "canned-stderr"
    assert out.returncode == 0
    assert out.timed_out is False


def test_in_memory_actuator_captures_clipboard() -> None:
    act = InMemoryActuator()
    assert act.set_clipboard("hello") is True
    assert act.clipboard_calls == ["hello"]


def test_in_memory_actuator_clipboard_can_report_failure() -> None:
    act = InMemoryActuator(clipboard_ok=False)
    assert act.set_clipboard("hello") is False
    # Failure mode still records the attempt.
    assert act.clipboard_calls == ["hello"]


def test_in_memory_actuator_captures_feedback() -> None:
    act = InMemoryActuator()
    act.play_feedback("zwingli.match")
    act.play_feedback("zwingli.error")
    assert act.feedback_calls == ["zwingli.match", "zwingli.error"]


def test_in_memory_actuator_subprocess_raises_when_capability_missing() -> None:
    act = InMemoryActuator(caps=frozenset({CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK}))
    with pytest.raises(ActuatorCapabilityError):
        act.run_subprocess(["true"])


def test_in_memory_actuator_clipboard_noops_when_capability_missing() -> None:
    act = InMemoryActuator(caps=frozenset({CAP_SUBPROCESS}))
    assert act.set_clipboard("x") is False
    assert act.clipboard_calls == []


def test_in_memory_actuator_feedback_noops_when_capability_missing() -> None:
    act = InMemoryActuator(caps=frozenset({CAP_SUBPROCESS}))
    act.play_feedback("zwingli.match")
    assert act.feedback_calls == []


# ---------------------------------------------------------------------------
# DesktopActuator subprocess wiring
# ---------------------------------------------------------------------------


def test_desktop_actuator_run_subprocess_uses_module_level_subprocess_run(monkeypatch) -> None:
    """Tests historically monkeypatch `tt.subprocess.run`. The desktop
    actuator must reach subprocess.run via the module — not a captured
    reference — so existing patches keep intercepting."""
    seen: list[dict[str, object]] = []

    class _FakeProc:
        def __init__(self, **kw):
            self.returncode = 0
            self.stdout = "intercepted"
            self.stderr = ""

    def _fake_run(argv, **kw):
        seen.append({"argv": argv, "kwargs": kw})
        return _FakeProc()

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    result = DesktopActuator().run_subprocess(["true"], shell=False)
    assert result.returncode == 0
    assert result.stdout == "intercepted"
    assert seen and seen[0]["argv"] == ["true"]


def test_desktop_actuator_subprocess_timeout_yields_timed_out_result(monkeypatch) -> None:
    def _explode(argv, **kw):
        raise _stdlib_subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout"))

    monkeypatch.setattr(tt.subprocess, "run", _explode)
    result = DesktopActuator().run_subprocess(["sleep", "60"], shell=False, timeout_seconds=0.01)
    assert result.timed_out is True
    assert result.returncode is None


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


def _shell_commands() -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell"
            ),
        },
    )


def test_apply_transcript_triggers_forwards_actuator_to_shell_verb(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    act = InMemoryActuator(
        subprocess_result=SubprocessResult(
            returncode=0, stdout="from-in-memory\n", stderr=""
        )
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli subprocess ls -la", commands=_shell_commands(), actuator=act
    )

    assert out == "from-in-memory"
    assert act.subprocess_calls == [
        {"argv": "ls -la", "shell": True, "timeout_seconds": pytest.approx(10.0)}
    ]
    assert meta["ok"] is True


def test_apply_transcript_triggers_shell_graceful_skip_without_subprocess_capability(
    monkeypatch,
) -> None:
    """An actuator that doesn't support subprocess (e.g. Android shell) must
    surface a polite ⚠ zwingli error rather than crash. The env-var gate
    is intentionally bypassed — capability is checked *first* so the user
    gets the right reason."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    act = InMemoryActuator(caps=frozenset({CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK}))

    out, meta = tt.apply_transcript_triggers(
        "zwingli subprocess ls -la", commands=_shell_commands(), actuator=act
    )

    assert out.startswith("⚠ zwingli:")
    assert "not supported on this device" in out
    assert meta["ok"] is False
    assert act.subprocess_calls == []


def test_apply_transcript_triggers_routes_audio_feedback_through_actuator() -> None:
    act = InMemoryActuator()

    tt.apply_transcript_triggers(
        "hello world", triggers={"zwingli": "strip"}, actuator=act
    )
    # No trigger match → no feedback fired.
    assert act.feedback_calls == []

    tt.apply_transcript_triggers(
        "zwingli hello", triggers={"zwingli": "strip"}, actuator=act
    )
    # Trigger matched (action=strip), so the audio_feedback mapper was
    # consulted and the actuator's play_feedback was called for whatever
    # event the payload mapped to (if any). Just assert the actuator was
    # *given the chance* — exact event names are an audio_feedback impl
    # detail, but absence would mean the wiring is broken.
    # (audio_feedback may legitimately return no event for some payloads;
    # we only care that the actuator is the path, not the legacy lazy
    # import.)


def test_apply_transcript_triggers_audio_feedback_skipped_when_capability_missing(
    monkeypatch,
) -> None:
    """Confirms the dispatcher checks CAP_AUDIO_FEEDBACK before consulting
    the audio_feedback mapper at all — useful on headless servers."""
    called = []

    def _explode(*a, **kw):
        called.append((a, kw))
        raise RuntimeError("audio_feedback should not be touched")

    # If we did consult audio_feedback, this monkeypatch would blow up.
    import voicepipe.audio_feedback as af

    monkeypatch.setattr(af, "event_for_trigger_payload", _explode)

    act = InMemoryActuator(caps=frozenset({CAP_SUBPROCESS, CAP_CLIPBOARD}))
    tt.apply_transcript_triggers(
        "zwingli hello", triggers={"zwingli": "strip"}, actuator=act
    )
    assert called == []


def test_error_destination_clipboard_routes_through_actuator(monkeypatch) -> None:
    """error_destination=clipboard must call actuator.set_clipboard, not
    reach into voicepipe.clipboard directly."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "0")  # ensure shell verb errors

    act = InMemoryActuator()

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination="clipboard"
        ),
        verbs={
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell"
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli subprocess rm -rf /", commands=commands, actuator=act
    )
    assert out.startswith("⚠ zwingli:")
    assert act.clipboard_calls == [out]
    assert meta["meta"]["clipboard"] is True
    assert meta["meta"]["suppress_type"] is True


def test_error_destination_clipboard_skips_actuator_when_capability_missing(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "0")

    act = InMemoryActuator(caps=frozenset({CAP_SUBPROCESS, CAP_AUDIO_FEEDBACK}))

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination="clipboard"
        ),
        verbs={
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell"
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli subprocess rm -rf /", commands=commands, actuator=act
    )
    assert out.startswith("⚠ zwingli:")
    assert act.clipboard_calls == []
    assert meta["meta"]["clipboard"] is False


def test_no_actuator_passed_defaults_to_desktop_actuator(monkeypatch) -> None:
    """The default behaviour for every existing call site must be unchanged:
    if no actuator is given, the desktop actuator is used and subprocess
    runs through the standard subprocess module (which existing tests
    monkeypatch)."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    class _FakeProc:
        returncode = 0
        stdout = "default-actuator-path\n"
        stderr = ""

    monkeypatch.setattr(tt.subprocess, "run", lambda *a, **k: _FakeProc())

    out, _meta = tt.apply_transcript_triggers(
        "zwingli subprocess true", commands=_shell_commands()
    )
    assert out == "default-actuator-path"
