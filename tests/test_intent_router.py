from __future__ import annotations

from voicepipe.intent_router import route_intent
from voicepipe.transcription_result import TranscriptionResult


def test_intent_router_routes_command_prefix() -> None:
    tr = TranscriptionResult(
        text="command copy that",
        backend="openai",
        model="gpt-test",
        audio_file="a.wav",
    )
    intent = route_intent(tr)
    assert intent.mode == "command"
    assert intent.command_text == "copy that"


def test_intent_router_routes_command_prefix_with_punctuation() -> None:
    tr = TranscriptionResult(
        text="Computer, open the browser",
        backend="openai",
        model="gpt-test",
        audio_file="a.wav",
    )
    intent = route_intent(tr)
    assert intent.mode == "command"
    assert intent.command_text == "open the browser"


def test_intent_router_defaults_to_dictation() -> None:
    tr = TranscriptionResult(
        text="hello world",
        backend="openai",
        model="gpt-test",
        audio_file="a.wav",
    )
    intent = route_intent(tr)
    assert intent.mode == "dictation"
    assert intent.dictation_text == "hello world"

