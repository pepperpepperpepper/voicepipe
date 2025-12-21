from __future__ import annotations

import importlib
import io
import signal
from pathlib import Path

import pytest


def _reload_backend():
    import voicepipe.session as session
    import voicepipe.recording_backend as recording_backend

    session = importlib.reload(session)
    recording_backend = importlib.reload(recording_backend)
    return session, recording_backend


def test_daemon_backend_unavailable_when_try_send_request_returns_none(monkeypatch) -> None:
    _session, rb = _reload_backend()
    monkeypatch.setattr(rb, "try_send_request", lambda *args, **kwargs: None)
    backend = rb.DaemonRecorderBackend()
    with pytest.raises(rb.BackendUnavailable):
        backend.status()


def test_daemon_backend_raises_recording_error_on_error_payload(monkeypatch) -> None:
    _session, rb = _reload_backend()
    monkeypatch.setattr(rb, "try_send_request", lambda *args, **kwargs: {"error": "boom"})
    backend = rb.DaemonRecorderBackend()
    with pytest.raises(rb.RecordingError) as exc:
        backend.status()
    assert "boom" in str(exc.value)


def test_subprocess_backend_start_sets_device_env(monkeypatch) -> None:
    _session, rb = _reload_backend()

    monkeypatch.setattr(rb.RecordingSession, "find_active_sessions", lambda: [])
    monkeypatch.setattr(rb.time, "sleep", lambda _s: None)

    captured: dict[str, object] = {}

    class _FakeProc:
        pid = 123

        def __init__(self):
            self.stderr = io.StringIO("")

        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(rb.subprocess, "Popen", fake_popen)

    backend = rb.SubprocessRecorderBackend()
    out = backend.start(device=12)
    assert out.mode == "subprocess"
    assert out.pid == 123
    assert isinstance(captured.get("env"), dict)
    assert captured["env"]["VOICEPIPE_DEVICE"] == "12"  # type: ignore[index]


def test_subprocess_backend_start_raises_on_early_exit(monkeypatch) -> None:
    _session, rb = _reload_backend()

    monkeypatch.setattr(rb.RecordingSession, "find_active_sessions", lambda: [])
    monkeypatch.setattr(rb.time, "sleep", lambda _s: None)

    class _FakeProc:
        pid = 123

        def __init__(self):
            self.stderr = io.StringIO("bad things happened")

        def poll(self):
            return 1

    monkeypatch.setattr(rb.subprocess, "Popen", lambda *a, **k: _FakeProc())

    backend = rb.SubprocessRecorderBackend()
    with pytest.raises(rb.RecordingError) as exc:
        backend.start(device=None)
    assert "bad things happened" in str(exc.value)


def test_subprocess_backend_stop_sends_sigterm(monkeypatch) -> None:
    _session, rb = _reload_backend()

    monkeypatch.setattr(rb.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        rb.RecordingSession,
        "get_current_session",
        lambda: {"pid": 777, "audio_file": "/tmp/a.wav"},
    )

    calls: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(rb.os, "kill", fake_kill)

    backend = rb.SubprocessRecorderBackend()
    out = backend.stop()
    assert out.mode == "subprocess"
    assert out.audio_file == "/tmp/a.wav"
    assert calls == [(777, signal.SIGTERM)]


def test_subprocess_backend_cancel_cleans_up_audio_file(tmp_path: Path, monkeypatch) -> None:
    _session, rb = _reload_backend()

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"123")

    session_dict = {"pid": 111, "audio_file": str(audio)}
    monkeypatch.setattr(rb.RecordingSession, "get_current_session", lambda: session_dict)

    cleaned: list[dict] = []

    monkeypatch.setattr(rb.RecordingSession, "cleanup_session", lambda s: cleaned.append(s))
    monkeypatch.setattr(rb.os, "kill", lambda *_a, **_k: None)

    backend = rb.SubprocessRecorderBackend()
    out = backend.cancel()
    assert out.mode == "subprocess"
    assert not audio.exists()
    assert cleaned == [session_dict]


def test_subprocess_backend_status_idle_when_no_session(monkeypatch) -> None:
    _session, rb = _reload_backend()

    monkeypatch.setattr(
        rb.RecordingSession, "get_current_session", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    backend = rb.SubprocessRecorderBackend()
    out = backend.status()
    assert out.status == "idle"


def test_auto_backend_falls_back_to_subprocess(monkeypatch) -> None:
    _session, rb = _reload_backend()

    backend = rb.AutoRecorderBackend()
    monkeypatch.setattr(backend._daemon, "start", lambda **_k: (_ for _ in ()).throw(rb.BackendUnavailable("no")))
    monkeypatch.setattr(
        backend._subprocess, "start", lambda **_k: rb.StartResult(mode="subprocess", pid=999)
    )
    out = backend.start(device=None)
    assert out.mode == "subprocess"
    assert out.pid == 999


def test_auto_backend_stop_prefers_subprocess_when_daemon_idle(monkeypatch) -> None:
    _session, rb = _reload_backend()

    backend = rb.AutoRecorderBackend()

    monkeypatch.setattr(
        backend._daemon,
        "status",
        lambda: rb.StatusResult(mode="daemon", status="idle", pid=123),
    )
    monkeypatch.setattr(
        backend._subprocess,
        "status",
        lambda: rb.StatusResult(mode="subprocess", status="recording", pid=777),
    )
    monkeypatch.setattr(backend._daemon, "stop", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        backend._subprocess,
        "stop",
        lambda: rb.StopResult(mode="subprocess", audio_file="/tmp/a.wav", session={}),
    )

    out = backend.stop()
    assert out.mode == "subprocess"
    assert out.audio_file == "/tmp/a.wav"


def test_auto_backend_stop_prefers_daemon_when_recording(monkeypatch) -> None:
    _session, rb = _reload_backend()

    backend = rb.AutoRecorderBackend()

    monkeypatch.setattr(
        backend._daemon,
        "status",
        lambda: rb.StatusResult(mode="daemon", status="recording", pid=123),
    )
    monkeypatch.setattr(
        backend._subprocess,
        "status",
        lambda: rb.StatusResult(mode="subprocess", status="recording", pid=777),
    )
    monkeypatch.setattr(
        backend._daemon,
        "stop",
        lambda: rb.StopResult(mode="daemon", audio_file="/tmp/d.wav", session=None),
    )
    monkeypatch.setattr(backend._subprocess, "stop", lambda: (_ for _ in ()).throw(AssertionError()))

    out = backend.stop()
    assert out.mode == "daemon"
    assert out.audio_file == "/tmp/d.wav"


def test_auto_backend_cancel_prefers_subprocess_when_daemon_idle(monkeypatch) -> None:
    _session, rb = _reload_backend()

    backend = rb.AutoRecorderBackend()

    monkeypatch.setattr(
        backend._daemon,
        "status",
        lambda: rb.StatusResult(mode="daemon", status="idle", pid=123),
    )
    monkeypatch.setattr(
        backend._subprocess,
        "status",
        lambda: rb.StatusResult(mode="subprocess", status="recording", pid=777),
    )
    monkeypatch.setattr(backend._daemon, "cancel", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        backend._subprocess,
        "cancel",
        lambda: rb.CancelResult(mode="subprocess"),
    )

    out = backend.cancel()
    assert out.mode == "subprocess"


def test_auto_backend_status_prefers_subprocess_when_daemon_idle(monkeypatch) -> None:
    _session, rb = _reload_backend()

    backend = rb.AutoRecorderBackend()

    monkeypatch.setattr(
        backend._daemon,
        "status",
        lambda: rb.StatusResult(mode="daemon", status="idle", pid=123),
    )
    monkeypatch.setattr(
        backend._subprocess,
        "status",
        lambda: rb.StatusResult(mode="subprocess", status="recording", pid=777),
    )

    out = backend.status()
    assert out.mode == "subprocess"
    assert out.status == "recording"
