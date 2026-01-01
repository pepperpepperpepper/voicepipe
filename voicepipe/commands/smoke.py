"""`voicepipe smoke` command.

This is intended for manual, end-to-end verification in a real environment
(audio deps + network + API key for your configured backend). It is not used by
the offline pytest suite.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from voicepipe.config import get_transcribe_model
from voicepipe.transcription import transcribe_audio_file
from voicepipe.typing import type_text


_FALLBACK_EXPECTED = "ask not what your country can do for you"


def _default_sample_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "test.mp3"


def _default_expected_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "test.expected.txt"


def _load_default_expected() -> str:
    try:
        text = _default_expected_path().read_text(encoding="utf-8").strip()
        return text or _FALLBACK_EXPECTED
    except Exception:
        return _FALLBACK_EXPECTED


def _normalize(text: str) -> str:
    # Lowercase, drop punctuation, and collapse whitespace so checks are resilient
    # to minor formatting differences.
    lowered = text.lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split()).strip()


@click.command("smoke")
@click.argument(
    "audio_file",
    required=False,
    type=click.Path(exists=False, dir_okay=False, readable=True),
)
@click.option(
    "--expected",
    default=None,
    show_default="voicepipe/assets/test.expected.txt",
    help="Substring expected in the transcript (case/punctuation-insensitive).",
)
@click.option(
    "--type",
    "type_",
    is_flag=True,
    help="Type the transcribed text using the configured typing backend (see VOICEPIPE_TYPE_BACKEND)",
)
@click.option("--language", help="Language code for transcription (e.g., en, es, fr)")
@click.option(
    "--prompt",
    help=(
        "Context prompt to guide transcription style. For dictation with quotes, "
        'say "open quote" and "close quote"'
    ),
)
@click.option(
    "--model",
    default=None,
    help=(
        "Transcription model to use (defaults to VOICEPIPE_TRANSCRIBE_MODEL / "
        "VOICEPIPE_MODEL; backend via VOICEPIPE_TRANSCRIBE_BACKEND). "
        "You can also prefix: openai:whisper-1 or elevenlabs:scribe_v1"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
@click.option(
    "--prefer-daemon/--no-daemon",
    default=True,
    show_default=True,
    help="Prefer the transcriber daemon when available.",
)
def smoke(
    audio_file: str | None,
    expected: str | None,
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
    prefer_daemon: bool,
) -> None:
    """Transcribe a known sample file (or a provided file) and sanity-check output."""
    sample_path = _default_sample_path()
    path = Path(audio_file) if audio_file else sample_path
    if not path.exists():
        raise click.ClickException(
            f"Audio file not found: {path}\n"
            "If you installed from source, run from the repo root.\n"
            "If you installed via pip, ensure package assets are present."
        )

    resolved_model = (model or get_transcribe_model()).strip()
    expected_text = _load_default_expected() if expected is None else expected
    text = transcribe_audio_file(
        str(path),
        model=resolved_model,
        language=language,
        prompt=prompt,
        temperature=float(temperature),
        prefer_daemon=bool(prefer_daemon),
    )

    click.echo(text)

    if type_:
        ok, err = type_text(text)
        if not ok:
            click.echo(f"Error typing text: {err}", err=True)

    want = _normalize(expected_text or "")
    got = _normalize(text or "")

    if want and want not in got:
        raise click.ClickException(
            "Smoke test failed: transcript did not contain expected text.\n\n"
            f"expected: {expected_text}\n"
            f"file: {path}\n"
            f"model: {resolved_model}"
        )
