from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.paths import preserved_audio_dir
from voicepipe.transcription_result import TranscriptionResult


@dataclass(frozen=True)
class _StopResult:
    audio_file: str
    session: dict[str, Any] | None
    recording_id: str | None = None


def test_stop_deletes_audio_file_on_success(tmp_path: Path, monkeypatch, isolated_home: Path) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    session_dict = {"pid": 1, "audio_file": str(audio)}

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=session_dict)

    cleaned: list[dict[str, Any]] = []

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(
        recording_cmd,
        "transcribe_audio_file_result",
        lambda *_a, **_k: TranscriptionResult(
            text="hello",
            backend="openai",
            model="gpt-test",
            audio_file=str(audio),
            recording_id=None,
            source="stop",
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        recording_cmd.RecordingSession, "cleanup_session", lambda s: cleaned.append(s)
    )

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, result.output
    assert "hello" in result.output
    assert not audio.exists()
    assert cleaned == [session_dict]


def test_stop_preserves_audio_file_on_transcription_error(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(recording_cmd, "transcribe_audio_file_result", _boom)

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 1
    assert "Error: boom" in result.stderr
    assert "Preserved audio file:" in result.stderr

    preserved_dir = preserved_audio_dir()
    preserved = preserved_dir / audio.name
    assert preserved.exists()
    assert not audio.exists()


def test_stop_json_outputs_structured_result(tmp_path: Path, monkeypatch, isolated_home: Path) -> None:
    import json as _json

    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    session_dict = {"pid": 1, "audio_file": str(audio)}

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=session_dict, recording_id="rid123")

    def _fake_transcribe(audio_file: str, **kwargs):
        assert audio_file == str(audio)
        assert kwargs.get("recording_id") == "rid123"
        assert kwargs.get("source") == "stop"
        return TranscriptionResult(
            text="hello",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        )

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setattr(recording_cmd, "transcribe_audio_file_result", _fake_transcribe)

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--json"])
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output.strip())
    assert payload["text"] == "hello"
    assert payload["backend"] == "openai"
    assert payload["model"] == "gpt-test"
    assert payload["recording_id"] == "rid123"
    assert payload["source"] == "stop"
    assert payload["intent"]["mode"] == "dictation"
    assert payload["intent"]["dictation_text"] == "hello"
    assert "sk-test-secret" not in result.output
