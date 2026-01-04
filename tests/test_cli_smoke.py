from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from voicepipe.cli import main


def test_smoke_passes_when_expected_substring_present(tmp_path: Path, monkeypatch) -> None:
    import voicepipe.commands.smoke as smoke_cmd

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"not really mp3")

    monkeypatch.setattr(
        smoke_cmd,
        "transcribe_audio_file",
        lambda *_a, **_k: "Ask not what your country can do for you.",
    )

    runner = CliRunner()
    result = runner.invoke(main, ["smoke", str(audio), "--expected", "ask not what your country can do for you"])
    assert result.exit_code == 0, result.output


def test_smoke_fails_when_expected_substring_missing(tmp_path: Path, monkeypatch) -> None:
    import voicepipe.commands.smoke as smoke_cmd

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"not really mp3")

    monkeypatch.setattr(smoke_cmd, "transcribe_audio_file", lambda *_a, **_k: "completely different")

    runner = CliRunner()
    result = runner.invoke(main, ["smoke", str(audio), "--expected", "needle"])
    assert result.exit_code != 0
    assert "Smoke test failed" in result.output

