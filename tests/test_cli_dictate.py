from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from voicepipe.cli import main


@dataclass(frozen=True)
class _StopResult:
    audio_file: str
    session: dict[str, Any] | None


def test_dictate_requires_seconds_without_tty(monkeypatch) -> None:
    runner = CliRunner()
    # Under CliRunner, stdin is not a TTY, so the default (wait for ENTER) should fail.
    result = runner.invoke(main, ["dictate"])
    assert result.exit_code != 0
    assert "No TTY available" in result.output


def test_dictate_records_transcribes_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def start(self, *, device):
            return None

        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

        def cancel(self):
            return None

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(recording_cmd, "transcribe_audio_file", lambda *_a, **_k: "hello")
    monkeypatch.setattr(recording_cmd, "type_text", lambda *_a, **_k: (True, None))

    runner = CliRunner()
    result = runner.invoke(main, ["dictate", "--seconds", "0.01"])
    assert result.exit_code == 0, result.output
    assert "hello" in result.output
    assert not audio.exists()

