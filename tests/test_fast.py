from __future__ import annotations

from pathlib import Path

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


def test_fast_send_cmd_does_not_pin_socket_path(monkeypatch) -> None:
    import voicepipe.fast as fast

    seen: dict[str, object] = {}

    def _fake_send_request(command: str, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "command": command}

    monkeypatch.setattr(fast, "send_request", _fake_send_request)
    out = fast.send_cmd("status")
    assert out["ok"] is True
    assert out["command"] == "status"
    assert "socket_path" not in seen


def test_fast_send_transcribe_request_prints_warning_but_runs(monkeypatch, capsys) -> None:
    import voicepipe.fast as fast

    monkeypatch.setattr(fast, "find_transcriber_socket_path", lambda: None)
    monkeypatch.setattr(fast, "transcriber_socket_paths", lambda: [Path("/tmp/missing.sock")])
    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")
    monkeypatch.setattr(
        fast,
        "transcribe_audio_file",
        lambda audio_file, **kwargs: f"OK:{audio_file}:{kwargs.get('model')}",
    )

    assert fast.send_transcribe_request("a.wav") == "OK:a.wav:gpt-test"
    out = capsys.readouterr()
    assert "Transcriber socket not found" in out.err

