"""ElevenLabs TTS helper for synthesized-voice round-trip tests.

`synthesize(text)` returns a Path to an MP3 of the spoken phrase. The
MP3s are content-addressed (hash of text|voice|model) and committed to
``tests/synth_cache/``, so CI never needs an ElevenLabs key — only the
developer adding a new test phrase does. On a cache miss without a key,
synthesize raises with an instructive message.

Key resolution order:
  1. ELEVENLABS_API_KEY / XI_API_KEY environment variable
  2. ~/.api-keys (supports both `KEY=value` and `export KEY=value` lines)

Uses stdlib urllib to avoid adding a test-only dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional
from urllib import error, request


# Rachel — ElevenLabs' standard demo voice. Clean American English, transcribes
# reliably with OpenAI gpt-4o-transcribe. Changing this invalidates the cache.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
DEFAULT_MODEL = "eleven_turbo_v2_5"

CACHE_DIR = Path(__file__).resolve().parent / "synth_cache"


def _cache_key(text: str, voice_id: str, model: str) -> str:
    h = hashlib.sha1()
    h.update(text.encode("utf-8"))
    h.update(b"|")
    h.update(voice_id.encode("utf-8"))
    h.update(b"|")
    h.update(model.encode("utf-8"))
    return h.hexdigest()[:16]


def _resolve_api_key() -> Optional[str]:
    for name in ("ELEVENLABS_API_KEY", "XI_API_KEY"):
        val = (os.environ.get(name) or "").strip()
        if val:
            return val

    api_keys = Path.home() / ".api-keys"
    if not api_keys.is_file():
        return None
    try:
        text = api_keys.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        for key_name in ("ELEVENLABS_API_KEY", "XI_API_KEY"):
            prefix = f"{key_name}="
            if line.startswith(prefix):
                value = line[len(prefix) :].strip()
                if value and value[0] in "\"'" and value[-1] == value[0]:
                    value = value[1:-1]
                if value:
                    return value
    return None


def _slugify(text: str, max_len: int = 40) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:max_len].strip("-")


def synthesize(
    text: str,
    *,
    voice_id: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
) -> Path:
    """Return a cached MP3 path for `text` rendered via ElevenLabs TTS.

    First call hashes (text, voice, model), generates the MP3, caches it
    under tests/synth_cache, and returns the path. Subsequent calls (or
    CI runs against a committed cache) skip the API call entirely.

    Raises RuntimeError on a cache miss when no ElevenLabs key is
    available — the test author should commit the generated MP3.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = _cache_key(text, voice_id, model)
    out = CACHE_DIR / f"{key}-{_slugify(text)}.mp3"
    if out.exists():
        return out

    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError(
            f"No cached synth for {text!r} at {out.name}, and "
            "ELEVENLABS_API_KEY isn't set (also checked ~/.api-keys). "
            "Set the key locally, run the test once to generate, then "
            "commit the resulting MP3."
        )

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    body = json.dumps(
        {
            "text": text,
            "model_id": model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            audio = resp.read()
    except error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"ElevenLabs TTS failed for {text!r} ({e.code}): {detail}"
        ) from e
    except error.URLError as e:
        raise RuntimeError(
            f"ElevenLabs TTS network error for {text!r}: {e.reason}"
        ) from e

    if not audio:
        raise RuntimeError(f"ElevenLabs TTS returned empty audio for {text!r}")
    out.write_bytes(audio)
    return out
