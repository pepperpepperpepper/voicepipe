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


def _combined_cli_output(result) -> str:
    out = getattr(result, "output", "") or ""
    try:
        err = getattr(result, "stderr", "") or ""
    except ValueError:
        err = ""
    return out + err


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


def test_stop_keep_audio_flag_keeps_audio_file_on_success(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
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
    result = runner.invoke(main, ["stop", "--keep-audio"])
    assert result.exit_code == 0, _combined_cli_output(result)
    combined = _combined_cli_output(result)
    assert "hello" in combined
    assert "Kept audio file:" in combined
    assert audio.exists()
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
    combined = _combined_cli_output(result)
    assert "Error: boom" in combined
    assert "Preserved audio file:" in combined

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


def test_stop_clipboard_verb_skips_typing_and_copies(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None, recording_id="rid")

    triggered: dict[str, str] = {}

    def _fake_copy(text: str):
        triggered["copied"] = text
        return True, None

    typed: list[str] = []

    def _fake_type(text: str, **kwargs):
        typed.append(text)
        return True, None

    def _fake_transcribe(audio_file: str, **kwargs):
        # Simulate that apply_transcript_triggers ran upstream and attached
        # the clipboard verb's trigger metadata + suppress_type flag.
        return TranscriptionResult(
            text="hello world",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
            transcript_trigger={
                "action": "dispatch",
                "meta": {
                    "mode": "verb",
                    "verb": "copy",
                    "action": "clipboard",
                    "handler_meta": {"clipboard": True, "suppress_type": True},
                },
            },
        )

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(recording_cmd, "transcribe_audio_file_result", _fake_transcribe)
    monkeypatch.setattr(recording_cmd, "type_text", _fake_type)

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--type"])
    assert result.exit_code == 0, _combined_cli_output(result)
    assert typed == []


def test_stop_routes_zwingli_command_via_triggers_when_enabled(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import json as _json

    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None, recording_id="rid")

    def _fake_transcribe(audio_file: str, **kwargs):
        return TranscriptionResult(
            text="zwingli hello world",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        )

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(recording_cmd, "transcribe_audio_file_result", _fake_transcribe)
    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING_ENABLED", "1")
    monkeypatch.setenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", "zwingli=strip")
    monkeypatch.setenv("VOICEPIPE_COMMANDS_STRICT", "1")

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--json"])
    assert result.exit_code == 2, _combined_cli_output(result)

    json_line = result.output.splitlines()[0]
    payload = _json.loads(json_line)
    assert payload["intent"]["mode"] == "command"
    assert payload["intent"]["command_text"] == "hello world"
    assert payload["intent"]["reason"] == "trigger:zwingli"


def _install_destination_fakes(
    monkeypatch,
    audio: Path,
    destination: str,
) -> tuple[list[str], list[str]]:
    import voicepipe.clipboard as clipboard_mod
    import voicepipe.commands.recording as recording_cmd

    session_dict = {"pid": 1, "audio_file": str(audio)}

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=session_dict)

    clipboard_calls: list[str] = []
    typed: list[str] = []

    def _fake_copy(text: str):
        clipboard_calls.append(text)
        return True, None

    def _fake_type(text: str, **_kwargs):
        typed.append(text)
        return True, None

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)
    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(recording_cmd, "type_text", _fake_type)
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
            transcript_trigger={
                "ok": True,
                "action": "dispatch",
                "trigger": "zwingli",
                "reason": "prefix",
                "meta": {"destination": destination},
            },
            warnings=[],
        ),
    )
    return clipboard_calls, typed


def test_stop_clipboard_destination_copies_without_flags(
    tmp_path: Path,
    monkeypatch,
    isolated_home: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    clipboard_calls, typed = _install_destination_fakes(monkeypatch, audio, "clipboard")

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "hello"
    assert clipboard_calls == ["hello"]
    assert typed == []


def test_stop_clipboard_destination_overrides_type_flag(
    tmp_path: Path,
    monkeypatch,
    isolated_home: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    clipboard_calls, typed = _install_destination_fakes(monkeypatch, audio, "clipboard")

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--type"])
    assert result.exit_code == 0, _combined_cli_output(result)
    assert clipboard_calls == ["hello"]
    assert typed == []


def test_stop_type_destination_types_without_flag(
    tmp_path: Path,
    monkeypatch,
    isolated_home: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    clipboard_calls, typed = _install_destination_fakes(monkeypatch, audio, "type")

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, _combined_cli_output(result)
    assert typed == ["hello"]
    assert clipboard_calls == []


def test_stop_type_destination_overrides_clipboard_flag(
    tmp_path: Path,
    monkeypatch,
    isolated_home: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    clipboard_calls, typed = _install_destination_fakes(monkeypatch, audio, "type")

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--clipboard"])
    assert result.exit_code == 0, _combined_cli_output(result)
    assert typed == ["hello"]
    assert clipboard_calls == []


def test_stop_both_destination_types_and_copies(
    tmp_path: Path,
    monkeypatch,
    isolated_home: Path,
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    clipboard_calls, typed = _install_destination_fakes(monkeypatch, audio, "both")

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, _combined_cli_output(result)
    assert typed == ["hello"]
    assert clipboard_calls == ["hello"]
