from __future__ import annotations

from pathlib import Path

from voicepipe.transcription_result import TranscriptionResult


def test_fast_send_transcribe_request_routes_command_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    import voicepipe.fast as fast

    sock = tmp_path / "transcriber.sock"
    sock.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fast, "TRANSCRIBER_SOCKET", sock)

    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")

    def _fake_transcribe(audio_file: str, **kwargs):
        return TranscriptionResult(
            text="zwingli copy that",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        )

    monkeypatch.setattr(fast, "transcribe_audio_file_result", _fake_transcribe)
    monkeypatch.setattr(
        fast,
        "process_zwingli_prompt",
        lambda prompt, **_k: f"LLM:{prompt}",
    )

    ok, text, payload = fast.send_transcribe_request(
        "a.wav", recording_id="rid123", source="fast-stop"
    )
    assert ok is True
    assert text == "LLM:copy that"
    assert payload["recording_id"] == "rid123"
    assert payload["intent"]["mode"] == "command"
    assert payload["intent"]["command_text"] == "copy that"
    assert payload["output_text"] == "LLM:copy that"


def test_fast_send_transcribe_request_strict_mode_refuses_command_output(
    tmp_path: Path, monkeypatch
) -> None:
    import voicepipe.fast as fast

    sock = tmp_path / "transcriber.sock"
    sock.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fast, "TRANSCRIBER_SOCKET", sock)

    monkeypatch.setenv("VOICEPIPE_COMMANDS_STRICT", "1")
    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")

    monkeypatch.setattr(
        fast,
        "transcribe_audio_file_result",
        lambda audio_file, **kwargs: TranscriptionResult(
            text="zwingli open browser",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        ),
    )

    called: list[str] = []
    monkeypatch.setattr(
        fast,
        "process_zwingli_prompt",
        lambda *_a, **_k: called.append("x") or "",
    )

    ok, text, payload = fast.send_transcribe_request("a.wav", source="fast-stop")
    assert ok is True
    assert text == ""
    assert payload["intent"]["mode"] == "command"
    assert called == []


def test_fast_send_transcribe_request_routing_disabled_does_not_strip_prefix(
    tmp_path: Path, monkeypatch
) -> None:
    import voicepipe.fast as fast

    sock = tmp_path / "transcriber.sock"
    sock.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fast, "TRANSCRIBER_SOCKET", sock)

    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "0")
    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")

    monkeypatch.setattr(
        fast,
        "transcribe_audio_file_result",
        lambda audio_file, **kwargs: TranscriptionResult(
            text="zwingli copy that",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        ),
    )

    ok, text, payload = fast.send_transcribe_request("a.wav", source="fast-stop")
    assert ok is True
    assert text == "zwingli copy that"
    assert payload["intent"]["mode"] == "dictation"
    assert payload["intent"]["reason"] == "disabled"


def test_fast_send_transcribe_request_custom_wake_prefixes(tmp_path: Path, monkeypatch) -> None:
    import voicepipe.fast as fast

    sock = tmp_path / "transcriber.sock"
    sock.write_text("x", encoding="utf-8")
    monkeypatch.setattr(fast, "TRANSCRIBER_SOCKET", sock)

    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "1")
    monkeypatch.setenv("VOICEPIPE_INTENT_WAKE_PREFIXES", "zwingli")
    monkeypatch.setattr(fast, "get_transcribe_model", lambda: "gpt-test")

    monkeypatch.setattr(
        fast,
        "transcribe_audio_file_result",
        lambda audio_file, **kwargs: TranscriptionResult(
            text="zwingli open browser",
            backend="openai",
            model="gpt-test",
            audio_file=audio_file,
            recording_id=kwargs.get("recording_id"),
            source=kwargs.get("source"),
            warnings=[],
        ),
    )
    monkeypatch.setattr(
        fast,
        "process_zwingli_prompt",
        lambda prompt, **_k: f"LLM:{prompt}",
    )

    ok, text, payload = fast.send_transcribe_request("a.wav", source="fast-stop")
    assert ok is True
    assert text == "LLM:open browser"
    assert payload["intent"]["mode"] == "command"
    assert payload["intent"]["command_text"] == "open browser"
