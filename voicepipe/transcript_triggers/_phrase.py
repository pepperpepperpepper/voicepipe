"""Activation-phrase normalization and validation.

Shared by :mod:`voicepipe.commands.triggers` (the ``voicepipe triggers
add/remove`` CLI) and :mod:`voicepipe.dispatch_server` (the
``PATCH /triggers`` endpoint) so the rules a phone-side configurator
applies are the same ones the server enforces.
"""

from __future__ import annotations

import re

# A trigger phrase is matched as a prefix of the transcript (followed by a
# space-or-end boundary), so we keep the allowed character set narrow:
# lowercase letters, optionally separated by single spaces. Digits and
# punctuation would interact poorly with the transcript boundary logic and
# with most speech recognizers (which spell out digits inconsistently).
PHRASE_MIN_LEN = 2
PHRASE_MAX_LEN = 40
PHRASE_RE = re.compile(r"^[a-z]+( [a-z]+)*$")


def normalize_phrase(raw: str) -> str:
    """Lowercase, trim, collapse runs of whitespace to single spaces."""
    return " ".join((raw or "").strip().lower().split())


def validate_phrase(phrase: str) -> str | None:
    """Return ``None`` if ``phrase`` is a legal trigger; otherwise a reason.

    Assumes the input has already been through :func:`normalize_phrase`.
    The reason string is meant to be surfaced to the user (CLI stderr,
    HTTP 400 body, configurator inline error) — keep it short.
    """
    if not phrase:
        return "phrase is empty"
    if len(phrase) < PHRASE_MIN_LEN:
        return f"phrase too short (min {PHRASE_MIN_LEN} chars)"
    if len(phrase) > PHRASE_MAX_LEN:
        return f"phrase too long (max {PHRASE_MAX_LEN} chars)"
    if not PHRASE_RE.match(phrase):
        return "phrase must be lowercase letters, optionally separated by single spaces"
    return None
