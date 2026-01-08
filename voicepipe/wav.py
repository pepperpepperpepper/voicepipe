"""Small WAV helpers (stdlib-only)."""

from __future__ import annotations

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

