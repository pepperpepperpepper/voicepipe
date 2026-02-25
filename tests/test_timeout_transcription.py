from __future__ import annotations

from pathlib import Path

from voicepipe.last_output import load_last_output, save_last_output
from voicepipe.paths import preserved_audio_dir
from voicepipe.timeout_transcription import transcribe_timeout_audio_file
from voicepipe.transcription_result import TranscriptionResult


def test_timeout_transcription_saves_last_output_and_deletes_audio(
    tmp_path: Path, monkeypatch, isolated_home
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    def _fake_transcribe(audio_file: str, *, model: str, recording_id=None, source=None, **_k):
        return TranscriptionResult(
            text="hello",
            backend="openai",
            model=model,
            audio_file=audio_file,
            recording_id=recording_id,
            source=source,
            warnings=[],
        )

    import voicepipe.timeout_transcription as timeout_transcription

    monkeypatch.setattr(timeout_transcription, "transcribe_audio_file_result", _fake_transcribe)
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_MODEL", "gpt-test")

    ok = transcribe_timeout_audio_file(
        str(audio),
        recording_id="rid",
        source="start-timeout",
        keep_audio=False,
    )
    assert ok is True
    assert not audio.exists()

    entry = load_last_output()
    assert entry is not None
    assert entry.text == "hello"
    assert entry.payload is not None
    assert entry.payload.get("timeout") is True
    assert entry.payload.get("recording_id") == "rid"
    assert entry.payload.get("source") == "start-timeout"


def test_timeout_transcription_preserves_audio_on_failure(
    tmp_path: Path, monkeypatch, isolated_home
) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"abc")

    save_last_output("prior", payload={"source": "prior"})

    def _fail(*_a, **_k):
        raise RuntimeError("boom")

    import voicepipe.timeout_transcription as timeout_transcription

    monkeypatch.setattr(timeout_transcription, "transcribe_audio_file_result", _fail)
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_MODEL", "gpt-test")

    ok = transcribe_timeout_audio_file(
        str(audio),
        recording_id="rid",
        source="start-timeout",
        keep_audio=False,
    )
    assert ok is False

    # Should not clobber the previous replay buffer.
    entry = load_last_output()
    assert entry is not None
    assert entry.text == "prior"

    # The audio should be preserved for retry.
    preserved_path = preserved_audio_dir(create=True) / audio.name
    assert preserved_path.exists()

