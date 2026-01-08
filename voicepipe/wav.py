"""Small WAV helpers (stdlib-only)."""

from __future__ import annotations

from typing import BinaryIO
import wave


def read_wav_duration_s(path: str) -> float | None:
    """Return duration in seconds for a WAV file, or None if unreadable."""
    try:
        with wave.open(path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        if rate <= 0:
            return None
        return float(frames) / float(rate)
    except Exception:
        return None


def pcm_duration_s(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    sample_width: int = 2,
) -> float | None:
    """Return duration in seconds for raw PCM (int16 by default)."""
    try:
        rate = int(sample_rate)
        ch = int(channels)
        width = int(sample_width)
    except Exception:
        return None
    if rate <= 0 or ch <= 0 or width <= 0:
        return None
    frame_bytes = ch * width
    if frame_bytes <= 0:
        return None
    return float(len(pcm)) / float(frame_bytes * rate)


def write_wav_pcm(
    fh: BinaryIO,
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
    sample_width: int = 2,
) -> None:
    """Write raw PCM into a WAV container on the given file-like object."""
    with wave.open(fh, "wb") as wf:
        wf.setnchannels(int(channels))
        wf.setsampwidth(int(sample_width))
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm)
