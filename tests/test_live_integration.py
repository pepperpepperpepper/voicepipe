from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.live


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize(text: str) -> str:
    lowered = (text or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split()).strip()


def _asset_path(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1] / "voicepipe" / "assets" / Path(*parts)


def _skip_unless_live_enabled() -> None:
    if not _env_flag("VOICEPIPE_LIVE_TESTS"):
        pytest.skip(
            "Live integration tests are opt-in. Set VOICEPIPE_LIVE_TESTS=1 to run.",
            allow_module_level=True,
        )


_skip_unless_live_enabled()


def test_live_transcribe_known_sample_contains_expected_phrase() -> None:
    from voicepipe.config import detect_openai_api_key
    from voicepipe.transcription import transcribe_audio_file

    if not detect_openai_api_key(load_env=True):
        pytest.skip(
            "OPENAI_API_KEY not configured (set OPENAI_API_KEY or run `voicepipe setup`)."
        )

    audio = _asset_path("test.mp3")
    expected = _asset_path("test.expected.txt").read_text(encoding="utf-8").strip()

    model = (os.environ.get("VOICEPIPE_LIVE_TRANSCRIBE_MODEL") or "whisper-1").strip()
    text = transcribe_audio_file(
        str(audio),
        model=model,
        language="en",
        temperature=0.0,
        prefer_daemon=False,
    )

    want = _normalize(expected)
    got = _normalize(text)
    assert want and want in got


def test_live_recording_can_capture_audio(tmp_path: Path) -> None:
    """Exercise the actual recording stack (requires a working input device)."""
    try:
        import sounddevice as sd  # noqa: F401
    except Exception:
        pytest.skip("sounddevice not installed; skipping live recording test")

    # Skip if no input devices are present / usable.
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as e:
        pytest.skip(f"sounddevice query failed: {e}")

    if not any(int(d.get("max_input_channels", 0) or 0) > 0 for d in devices):
        pytest.skip("No audio input devices detected; skipping live recording test")

    from voicepipe.recorder import AudioRecorder

    recorder = AudioRecorder(max_duration=None, pre_open=False)
    try:
        try:
            recorder.start_recording()
        except Exception as e:
            pytest.skip(f"Could not open an input stream: {e}")

        time.sleep(0.5)
        data = recorder.stop_recording()
        assert data and len(data) > 0

        out_wav = tmp_path / "voicepipe-live.wav"
        recorder.save_to_file(data, str(out_wav))
        assert out_wav.exists()
        assert out_wav.stat().st_size > 1000
    finally:
        try:
            recorder.cleanup()
        except Exception:
            pass

