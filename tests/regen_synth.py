"""Generate missing MP3s under tests/synth_cache/.

AST-scans test files for ``synthesize("...")`` call sites, then for any
phrase whose cache entry is missing, calls ElevenLabs TTS to render the
MP3 and writes it under the cache directory. Existing entries are
skipped so a re-run is a no-op.

Usage::

    python -m tests.regen_synth                       # scans test_synth_*.py
    python -m tests.regen_synth path/to/extra_test.py

Requires ELEVENLABS_API_KEY (read from env or ``~/.api-keys``). Used
only by developers adding new test phrases; pytest itself never calls
ElevenLabs.
"""
from __future__ import annotations

import ast
import glob
import json
import sys
from pathlib import Path
from urllib import error, request

from tests._synth import (
    CACHE_DIR,
    DEFAULT_MODEL,
    DEFAULT_VOICE,
    _cache_path,
    resolve_api_key,
)


def _phrases_in_file(path: Path) -> list[str]:
    """Return every string-literal first argument passed to any function
    named `synthesize` in the given file. Preserves source order."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_synth = (
            isinstance(func, ast.Name) and func.id == "synthesize"
        ) or (isinstance(func, ast.Attribute) and func.attr == "synthesize")
        if not is_synth or not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            out.append(arg0.value)
    return out


def _generate(text: str, api_key: str) -> bytes:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{DEFAULT_VOICE}"
    body = json.dumps(
        {
            "text": text,
            "model_id": DEFAULT_MODEL,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
    ).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )
    try:
        with request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except error.HTTPError as e:
        raise RuntimeError(
            f"ElevenLabs TTS failed for {text!r} ({e.code}): "
            f"{e.read().decode('utf-8', 'replace')}"
        ) from e
    except error.URLError as e:
        raise RuntimeError(
            f"ElevenLabs TTS network error for {text!r}: {e.reason}"
        ) from e


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        paths = [Path(p) for p in argv[1:]]
    else:
        tests_dir = Path(__file__).resolve().parent
        paths = [Path(p) for p in sorted(glob.glob(str(tests_dir / "test_synth_*.py")))]

    if not paths:
        print("(no test_synth_*.py files found)", file=sys.stderr)
        return 1

    seen: set[str] = set()
    phrases: list[str] = []
    for p in paths:
        if not p.exists():
            print(f"✗ no such file: {p}", file=sys.stderr)
            return 1
        for phrase in _phrases_in_file(p):
            if phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)

    if not phrases:
        print("(no synthesize() call sites found)")
        return 0

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    api_key: str | None = None

    generated = 0
    cached = 0
    for phrase in phrases:
        out = _cache_path(phrase, DEFAULT_VOICE, DEFAULT_MODEL)
        if out.exists():
            cached += 1
            continue
        if api_key is None:
            api_key = resolve_api_key()
            if not api_key:
                print(
                    "✗ ELEVENLABS_API_KEY not set (checked env and "
                    "~/.api-keys); cannot generate missing MP3s.",
                    file=sys.stderr,
                )
                return 1
        print(f"  generating: {phrase!r}")
        audio = _generate(phrase, api_key)
        if not audio:
            print(f"✗ ElevenLabs returned empty audio for {phrase!r}", file=sys.stderr)
            return 1
        out.write_bytes(audio)
        generated += 1

    print(
        f"Done: generated {generated} new, {cached} already cached, "
        f"{generated + cached} total"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
