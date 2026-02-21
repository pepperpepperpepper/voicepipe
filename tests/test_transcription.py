from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import tempfile
from pathlib import Path

import pytest

from voicepipe.transcription import (
    TranscriberDaemonUnavailable,
    TranscriptionError,
    _transcribe_via_daemon,
    transcribe_audio_file,
)

import sys

if sys.platform == "win32":  # pragma: no cover
    pytest.skip("AF_UNIX integration tests are skipped on Windows CI", allow_module_level=True)


@contextlib.contextmanager
def _unix_socket_path(tmp_path: Path, filename: str) -> Path:
    # macOS has a relatively small AF_UNIX path length limit; pytest's tmp_path
    # can exceed it under /private/var/folders/...
    if sys.platform == "darwin":
        with tempfile.TemporaryDirectory(prefix="vp-sock-", dir="/tmp") as d:
            yield Path(d) / filename
        return
    yield tmp_path / filename


def _start_unix_server(socket_path: Path, handler) -> threading.Thread:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def _run() -> None:
        try:
            conn, _ = server.accept()
            try:
                handler(conn)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        finally:
            try:
                server.close()
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def test_transcribe_via_daemon_requires_socket(tmp_path: Path) -> None:
    with pytest.raises(TranscriberDaemonUnavailable):
        _transcribe_via_daemon(
            "a.wav",
            model="m",
            language=None,
            prompt=None,
            temperature=0.0,
            socket_path=tmp_path / "missing.sock",
        )


def test_transcribe_via_daemon_streams_text_until_complete(tmp_path: Path) -> None:
    with _unix_socket_path(tmp_path, "transcriber.sock") as sock_path:
        def handler(conn: socket.socket) -> None:
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data.decode("utf-8"))
            assert req["audio_file"] == "a.wav"
            assert req["model"] == "gpt-test"
            assert req["language"] == "en"
            assert req["prompt"] == "ctx"
            assert req["temperature"] == 0.0

            conn.sendall(
                (json.dumps({"type": "transcription", "text": "hello "}) + "\n").encode()
            )
            conn.sendall(
                (json.dumps({"type": "transcription", "text": "world"}) + "\n").encode()
            )
            conn.sendall((json.dumps({"type": "complete"}) + "\n").encode())

        t = _start_unix_server(sock_path, handler)
        out = _transcribe_via_daemon(
            "a.wav",
            model="gpt-test",
            language="en",
            prompt="ctx",
            temperature=0.0,
            socket_path=sock_path,
            connect_timeout=1.0,
            read_timeout=1.0,
        )
        t.join(timeout=1.0)
        assert out == "hello world"


def test_transcribe_via_daemon_propagates_error(tmp_path: Path) -> None:
    with _unix_socket_path(tmp_path, "transcriber.sock") as sock_path:
        def handler(conn: socket.socket) -> None:
            # Drain request then respond with an error.
            _ = conn.recv(4096)
            conn.sendall((json.dumps({"type": "error", "message": "boom"}) + "\n").encode())

        _start_unix_server(sock_path, handler)
        with pytest.raises(TranscriptionError) as exc:
            _transcribe_via_daemon(
                "a.wav",
                model="m",
                language=None,
                prompt=None,
                temperature=0.0,
                socket_path=sock_path,
                connect_timeout=1.0,
                read_timeout=1.0,
            )
        assert "boom" in str(exc.value)


def test_transcribe_audio_file_falls_back_when_daemon_unavailable(monkeypatch) -> None:
    class _FakeTranscriber:
        def __init__(self, model: str):
            self.model = model

        def transcribe(self, audio_file: str, **_kwargs) -> str:
            return f"ok:{audio_file}:{self.model}"

    monkeypatch.setattr(
        "voicepipe.transcription._transcribe_via_daemon",
        lambda *args, **kwargs: (_ for _ in ()).throw(TranscriberDaemonUnavailable("nope")),
    )
    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeTranscriber)

    out = transcribe_audio_file("a.wav", model="m", prefer_daemon=True)
    assert out == "ok:a.wav:m"


def test_transcribe_audio_file_uses_elevenlabs_backend_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_BACKEND", "elevenlabs")

    monkeypatch.setattr(
        "voicepipe.transcription._transcribe_via_daemon",
        lambda *args, **kwargs: (_ for _ in ()).throw(TranscriberDaemonUnavailable("nope")),
    )

    calls = {}

    class _FakeElevenLabs:
        def __init__(self, model_id: str):
            calls["model_id"] = model_id

        def transcribe(self, audio_file: str, **_kwargs) -> str:
            calls["audio_file"] = audio_file
            return f"ok-eleven:{audio_file}"

    monkeypatch.setattr(
        "voicepipe.elevenlabs_transcriber.ElevenLabsTranscriber", _FakeElevenLabs
    )

    out = transcribe_audio_file("a.wav", model="scribe_v1", prefer_daemon=True)
    assert out == "ok-eleven:a.wav"
    assert calls["model_id"] == "scribe_v1"


def test_transcribe_audio_file_model_prefix_overrides_backend(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_BACKEND", "elevenlabs")

    class _FakeWhisper:
        def __init__(self, model: str):
            self.model = model

        def transcribe(self, audio_file: str, **_kwargs) -> str:
            return f"ok-openai:{audio_file}:{self.model}"

    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeWhisper)

    out = transcribe_audio_file("a.wav", model="openai:whisper-1", prefer_daemon=False)
    assert out == "ok-openai:a.wav:whisper-1"


def test_transcribe_audio_file_sends_abs_path_and_backend_prefixed_model_to_daemon(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOICEPIPE_DAEMON_MODE", "always")
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_BACKEND", "openai")

    captured: dict[str, object] = {}

    def _fake_daemon(audio_file: str, *, model: str, **_kwargs) -> str:
        captured["audio_file"] = audio_file
        captured["model"] = model
        return "ok"

    monkeypatch.setattr("voicepipe.transcription._transcribe_via_daemon", _fake_daemon)

    out = transcribe_audio_file("a.wav", model="gpt-4o-transcribe", prefer_daemon=True)
    assert out == "ok"
    assert captured["audio_file"] == os.path.abspath(os.path.expanduser("a.wav"))
    assert captured["model"] == "openai:gpt-4o-transcribe"
