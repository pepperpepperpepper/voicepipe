"""Cache reader for synthesized-voice test audio.

`synthesize(text)` returns the path to a committed MP3 in
``tests/synth_cache/``. There is *no* API fallback — pytest runs are
guaranteed offline. To add a new test phrase, append a
``synthesize("...")`` call to a test, then run::

    python -m tests.regen_synth

…which AST-scans the test files for synthesize() literals and fills
in any missing cache entries via ElevenLabs TTS (requiring the key
only at regen time, never at test time).

The cache key is sha1(text|voice|model)[:16], and the filename
includes a short slug of the phrase so committed files are scrutable
at a glance.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional


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


def _slugify(text: str, max_len: int = 40) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower())
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")[:max_len].strip("-")


def _cache_path(text: str, voice_id: str, model: str) -> Path:
    key = _cache_key(text, voice_id, model)
    return CACHE_DIR / f"{key}-{_slugify(text)}.mp3"


def resolve_api_key() -> Optional[str]:
    """Look up the ElevenLabs API key. Used only by regen_synth, not by
    synthesize() — but lives here so it's a single source of truth for
    key resolution policy."""
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


def synthesize(
    text: str,
    *,
    voice_id: str = DEFAULT_VOICE,
    model: str = DEFAULT_MODEL,
) -> Path:
    """Return the cached MP3 path for `text`. Raises FileNotFoundError
    with a clear regen hint if the cache is missing the entry — pytest
    never calls ElevenLabs."""
    out = _cache_path(text, voice_id, model)
    if not out.exists():
        raise FileNotFoundError(
            f"No cached synth for {text!r} at {out.name}. "
            "Run `python -m tests.regen_synth` to populate it "
            "(needs ELEVENLABS_API_KEY)."
        )
    return out
