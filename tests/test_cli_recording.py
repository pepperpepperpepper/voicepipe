from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from voicepipe.cli import main
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

    preserved_dir = Path(os.environ["XDG_STATE_HOME"]) / "voicepipe" / "audio"
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
    assert payload["output_text"] == "hello"
    assert "sk-test-secret" not in result.output


def test_stop_routing_disabled_does_not_strip_prefix(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "0")
    monkeypatch.setattr(
        recording_cmd,
        "transcribe_audio_file_result",
        lambda *_a, **_k: TranscriptionResult(
            text="zwingli copy that",
            backend="openai",
            model="gpt-test",
            audio_file=str(audio),
            recording_id=None,
            source="stop",
            warnings=[],
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "zwingli copy that"


def test_stop_json_routing_disabled_reports_disabled_intent(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import json as _json

    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret")
    monkeypatch.setattr(
        recording_cmd,
        "transcribe_audio_file_result",
        lambda *_a, **_k: TranscriptionResult(
            text="zwingli copy that",
            backend="openai",
            model="gpt-test",
            audio_file=str(audio),
            recording_id=None,
            source="stop",
            warnings=[],
        ),
    )

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--json"])
    assert result.exit_code == 0, result.output

    payload = _json.loads(result.output.strip())
    assert payload["text"] == "zwingli copy that"
    assert payload["intent"]["mode"] == "dictation"
    assert payload["intent"]["reason"] == "disabled"
    assert payload["intent"]["dictation_text"] == "zwingli copy that"
    assert payload["intent"]["command_text"] is None
    assert payload["output_text"] == "zwingli copy that"
    assert "sk-test-secret" not in result.output


def test_stop_command_mode_uses_zwingli_llm_output(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setattr(
        recording_cmd,
        "transcribe_audio_file_result",
        lambda *_a, **_k: TranscriptionResult(
            text="zwingli make it formal: hello",
            backend="openai",
            model="gpt-test",
            audio_file=str(audio),
            recording_id=None,
            source="stop",
            warnings=[],
        ),
    )

    seen: dict[str, str] = {}

    def _fake_zwingli(prompt: str, **_kwargs) -> str:
        seen["prompt"] = prompt
        return "Formal hello."

    monkeypatch.setattr(recording_cmd, "process_zwingli_prompt", _fake_zwingli)

    runner = CliRunner()
    result = runner.invoke(main, ["stop"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Formal hello."
    assert seen["prompt"] == "make it formal: hello"


def test_stop_command_mode_strict_refuses_llm_call(
    tmp_path: Path, monkeypatch, isolated_home: Path
) -> None:
    import voicepipe.commands.recording as recording_cmd

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    class _FakeBackend:
        def stop(self):
            return _StopResult(audio_file=str(audio), session=None)

    monkeypatch.setattr(recording_cmd, "AutoRecorderBackend", lambda: _FakeBackend())
    monkeypatch.setenv("VOICEPIPE_COMMANDS_STRICT", "1")
    monkeypatch.setattr(
        recording_cmd,
        "transcribe_audio_file_result",
        lambda *_a, **_k: TranscriptionResult(
            text="zwingli make it formal: hello",
            backend="openai",
            model="gpt-test",
            audio_file=str(audio),
            recording_id=None,
            source="stop",
            warnings=[],
        ),
    )

    called: list[str] = []
    monkeypatch.setattr(
        recording_cmd, "process_zwingli_prompt", lambda *_a, **_k: called.append("x")
    )

    runner = CliRunner()
    result = runner.invoke(main, ["stop", "--json"])
    assert result.exit_code == 2
    assert called == []
