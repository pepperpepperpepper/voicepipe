from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from voicepipe.cli import main


@dataclass(frozen=True)
class _StopResult:
    audio_file: str
    session: dict[str, Any] | None


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
    monkeypatch.setattr(recording_cmd, "transcribe_audio_file", lambda *_a, **_k: "hello")
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

    monkeypatch.setattr(recording_cmd, "transcribe_audio_file", _boom)

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 1
    assert "Error: boom" in result.stderr
    assert "Preserved audio file:" in result.stderr

    preserved_dir = Path(os.environ["XDG_STATE_HOME"]) / "voicepipe" / "audio"
    preserved = preserved_dir / audio.name
    assert preserved.exists()
    assert not audio.exists()
