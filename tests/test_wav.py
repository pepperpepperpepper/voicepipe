from __future__ import annotations

import io
import wave

from voicepipe.wav import pcm_duration_s, write_wav_pcm


def test_pcm_duration_s_round_trips() -> None:
    pcm = b"\x00\x00" * 16000  # 1s of silence at 16kHz mono int16
    assert pcm_duration_s(pcm, sample_rate=16000, channels=1) == 1.0


def test_write_wav_pcm_writes_valid_header() -> None:
    pcm = b"\x01\x00" * 8000  # 0.5s at 16kHz mono int16
    buf = io.BytesIO()
    write_wav_pcm(buf, pcm, sample_rate=16000, channels=1, sample_width=2)
    buf.seek(0)
    with wave.open(buf, "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 8000
