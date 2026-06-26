from __future__ import annotations

import io

import pytest

from voicepipe.transcriber import WhisperTranscriber


class _FakeAudioTranscriptions:
    def __init__(self) -> None:
        self.last_params: dict[str, object] = {}

    def create(self, **params: object) -> str:
        self.last_params = dict(params)
        return "hello world"


class _FakeAudio:
    def __init__(self) -> None:
        self.transcriptions = _FakeAudioTranscriptions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.audio = _FakeAudio()


def _make_transcriber(model: str) -> tuple[WhisperTranscriber, _FakeOpenAIClient]:
    transcriber = WhisperTranscriber.__new__(WhisperTranscriber)
    fake_client = _FakeOpenAIClient()
    transcriber.api_key = "test"
    transcriber.client = fake_client  # type: ignore[assignment]
    transcriber.model = model
    return transcriber, fake_client


@pytest.mark.parametrize("model", ["gpt-4o-transcribe", "gpt-4o-mini-transcribe"])
def test_transcribe_file_sets_tuned_server_vad_chunking(model: str) -> None:
    transcriber, fake = _make_transcriber(model)
    transcriber.transcribe_file(io.BytesIO(b"audio"))
    strategy = fake.audio.transcriptions.last_params.get("chunking_strategy")
    assert strategy == {
        "type": "server_vad",
        "threshold": 0.6,
        "silence_duration_ms": 700,
        "prefix_padding_ms": 300,
    }


def test_gpt4_prompt_includes_non_speech_directive() -> None:
    text = WhisperTranscriber.GPT4_PROMPT
    assert "Do not annotate non-speech sounds" in text
    for word in ("breaths", "sighs", "yawns", "coughs", "pauses", "laughter"):
        assert word in text, f"missing filler word {word!r} in GPT4_PROMPT"
    assert "[BLANK_AUDIO]" in text


def test_transcribe_file_sends_no_prompt_by_default() -> None:
    # gpt-4* models no longer auto-inject GPT4_PROMPT: that "dictation mode /
    # spoken words only" framing made gpt-4o-transcribe emit filler words.
    # With no caller-supplied prompt we send none, matching AnySoftKeyboard's
    # clean default output.
    transcriber, fake = _make_transcriber("gpt-4o-transcribe")
    transcriber.transcribe_file(io.BytesIO(b"audio"))
    assert "prompt" not in fake.audio.transcriptions.last_params


def test_transcribe_file_sends_only_the_caller_prompt() -> None:
    transcriber, fake = _make_transcriber("gpt-4o-transcribe")
    transcriber.transcribe_file(io.BytesIO(b"audio"), prompt="my custom hint")
    sent_prompt = fake.audio.transcriptions.last_params.get("prompt")
    assert sent_prompt == "my custom hint"
