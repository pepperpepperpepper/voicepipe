from __future__ import annotations

import pytest

from voicepipe.config import DEFAULT_GROQ_BASE_URL, DEFAULT_GROQ_TRANSCRIBE_MODEL
from voicepipe.transcription import (
    _resolve_backend_and_model,
    transcribe_audio_bytes,
    transcribe_audio_file,
)


def _install_fake_whisper(monkeypatch) -> list[dict]:
    """Replace WhisperTranscriber with a fake that records its constructor kwargs.

    The helper re-imports WhisperTranscriber at call time, so patching the
    source module (``voicepipe.transcriber``) is what takes effect.
    """
    created: list[dict] = []

    class _FakeWhisper:
        def __init__(self, api_key=None, model="gpt-4o-transcribe", base_url=None):
            self.api_key = api_key
            self.model = model
            self.base_url = base_url
            created.append({"api_key": api_key, "model": model, "base_url": base_url})

        def transcribe_bytes(self, audio_bytes, *, filename, language=None,
                             prompt=None, temperature=0.0, model=None):
            return f"text:{self.model}"

        def transcribe(self, audio_file, *, language=None, prompt=None, temperature=0.0):
            return f"text:{self.model}"

    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeWhisper)
    return created


def test_resolve_groq_prefix() -> None:
    backend, model_id, model_for_daemon = _resolve_backend_and_model(
        "groq:whisper-large-v3-turbo"
    )
    assert backend == "groq"
    assert model_id == "whisper-large-v3-turbo"
    assert model_for_daemon == "groq:whisper-large-v3-turbo"


def test_resolve_groq_via_env_backend(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_BACKEND", "groq")
    backend, model_id, model_for_daemon = _resolve_backend_and_model("whisper-large-v3-turbo")
    assert backend == "groq"
    assert model_id == "whisper-large-v3-turbo"
    assert model_for_daemon == "groq:whisper-large-v3-turbo"


def test_bytes_groq_points_client_at_groq(monkeypatch) -> None:
    created = _install_fake_whisper(monkeypatch)
    monkeypatch.setattr(
        "voicepipe.transcription.get_groq_api_key", lambda *a, **k: "groq-test-key"
    )

    out = transcribe_audio_bytes(
        b"audio",
        filename="clip.wav",
        model=f"groq:{DEFAULT_GROQ_TRANSCRIBE_MODEL}",
        apply_triggers=False,
    )

    assert out == f"text:{DEFAULT_GROQ_TRANSCRIBE_MODEL}"
    assert len(created) == 1
    assert created[0]["base_url"] == DEFAULT_GROQ_BASE_URL
    assert created[0]["api_key"] == "groq-test-key"
    assert created[0]["model"] == DEFAULT_GROQ_TRANSCRIBE_MODEL


def test_bytes_openai_path_is_unchanged(monkeypatch) -> None:
    # Regression: the Linux desktop path (gpt-4o-transcribe) must NOT be
    # repointed at Groq and must never fetch the Groq key.
    created = _install_fake_whisper(monkeypatch)
    monkeypatch.setattr(
        "voicepipe.transcription.get_groq_api_key",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("groq key fetched on the openai path")
        ),
    )

    out = transcribe_audio_bytes(
        b"audio",
        filename="clip.wav",
        model="openai:gpt-4o-transcribe",
        apply_triggers=False,
    )

    assert out == "text:gpt-4o-transcribe"
    assert created[0]["base_url"] is None


def test_file_groq_direct(monkeypatch, tmp_path) -> None:
    created = _install_fake_whisper(monkeypatch)
    monkeypatch.setattr(
        "voicepipe.transcription.get_groq_api_key", lambda *a, **k: "groq-test-key"
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio")

    out = transcribe_audio_file(
        str(audio),
        model=f"groq:{DEFAULT_GROQ_TRANSCRIBE_MODEL}",
        prefer_daemon=False,
        apply_triggers=False,
    )

    assert out == f"text:{DEFAULT_GROQ_TRANSCRIBE_MODEL}"
    assert created[0]["base_url"] == DEFAULT_GROQ_BASE_URL
    assert created[0]["api_key"] == "groq-test-key"
