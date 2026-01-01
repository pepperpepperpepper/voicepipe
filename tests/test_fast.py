from __future__ import annotations

import pytest


def test_fast_main_help_prints_usage(capsys) -> None:
    import voicepipe.fast as fast

    with pytest.raises(SystemExit) as exc:
        fast.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert "Usage: voicepipe-fast" in out.out


def test_fast_main_unknown_command_errors(capsys) -> None:
    import voicepipe.fast as fast

    with pytest.raises(SystemExit) as exc:
        fast.main(["nope"])
    assert exc.value.code == 2
    out = capsys.readouterr()
    assert "unknown command" in out.err


def test_fast_send_transcribe_request_runs(monkeypatch) -> None:
    import voicepipe.fast as fast

    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")
    monkeypatch.setattr(
        "voicepipe.transcription.transcribe_audio_file",
        lambda audio_file, **kwargs: f"OK:{audio_file}:{kwargs.get('model')}",
    )

    assert fast.send_transcribe_request("a.wav") == "OK:a.wav:gpt-test"


def test_fast_main_does_not_crash_when_stdio_missing(monkeypatch) -> None:
    import voicepipe.fast as fast

    monkeypatch.setattr(fast.sys, "stderr", None)
    monkeypatch.setattr(fast.sys, "stdout", None)
    with pytest.raises(SystemExit) as exc:
        fast.main(["--help"])
    assert exc.value.code == 0
