from __future__ import annotations

import io

from voicepipe.transcription import (
    transcribe_audio_bytes,
    transcribe_audio_fileobj,
    transcribe_audio_fileobj_result,
)


def test_transcribe_audio_fileobj_uses_openai_backend(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class _FakeWhisper:
        def __init__(self, model: str):
            calls["model"] = model

        def transcribe_fileobj(self, fh, *, filename: str, **_kwargs) -> str:
            calls["filename"] = filename
            calls["data"] = fh.read()
            return f"ok-fileobj:{filename}:{calls['model']}"

    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeWhisper)

    fh = io.BytesIO(b"abc")
    out = transcribe_audio_fileobj(fh, filename="a.wav", model="m")
    assert out == "ok-fileobj:a.wav:m"
    assert calls["data"] == b"abc"


def test_transcribe_audio_fileobj_result_sets_audio_file_none(monkeypatch) -> None:
    class _FakeWhisper:
        def __init__(self, model: str):
            self.model = model

        def transcribe_fileobj(self, _fh, *, filename: str, **_kwargs) -> str:
            return f"ok-fileobj:{filename}:{self.model}"

    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeWhisper)

    fh = io.BytesIO(b"abc")
    result = transcribe_audio_fileobj_result(fh, filename="a.wav", model="m", source="test")
    assert result.text == "ok-fileobj:a.wav:m"
    assert result.audio_file is None
    assert result.backend == "openai"
    assert result.model == "m"
    assert result.source == "test"


def test_transcribe_audio_bytes_uses_openai_backend(monkeypatch) -> None:
    class _FakeWhisper:
        def __init__(self, model: str):
            self.model = model

        def transcribe_bytes(self, audio_bytes: bytes, *, filename: str, **_kwargs) -> str:
            assert audio_bytes == b"abc"
            return f"ok-bytes:{filename}:{self.model}"

    monkeypatch.setattr("voicepipe.transcriber.WhisperTranscriber", _FakeWhisper)

    out = transcribe_audio_bytes(b"abc", filename="a.wav", model="m")
    assert out == "ok-bytes:a.wav:m"
