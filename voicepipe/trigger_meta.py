"""Helpers for interpreting transcript-trigger metadata.

A "trigger" here is the dict returned by `apply_transcript_triggers` (also
stored on `TranscriptionResult.transcript_trigger`). The functions below take
that dict directly so the same logic serves both the CLI stop path
(recording.py) and the fast in-process toggle (fast.py) without duplication.
"""

from __future__ import annotations

from typing import Any


_VERB_DESTINATIONS = frozenset({"type", "clipboard", "both"})


def _meta_dict(trigger: object) -> dict[str, Any] | None:
    if not isinstance(trigger, dict):
        return None
    meta = trigger.get("meta")
    return meta if isinstance(meta, dict) else None


def extract_destination(trigger: object) -> str | None:
    meta = _meta_dict(trigger)
    if meta is None:
        return None
    raw = meta.get("destination")
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip().lower()
    return cleaned if cleaned in _VERB_DESTINATIONS else None


def suppresses_type(trigger: object) -> bool:
    """True if the trigger handler signaled it already emitted output."""
    meta = _meta_dict(trigger)
    if meta is None:
        return False
    if meta.get("suppress_type") is True:
        return True
    handler_meta = meta.get("handler_meta")
    if isinstance(handler_meta, dict) and handler_meta.get("suppress_type") is True:
        return True
    return False


def is_execute(trigger: object) -> bool:
    meta = _meta_dict(trigger)
    if meta is None:
        return False
    if meta.get("enter") is True:
        return True
    handler_meta = meta.get("handler_meta")
    if isinstance(handler_meta, dict) and handler_meta.get("enter") is True:
        return True
    return str(meta.get("verb_type") or "").strip().lower() == "execute"


def extract_type_sequence(trigger: object) -> list[dict[str, object]] | None:
    if not isinstance(trigger, dict):
        return None
    action = str(trigger.get("action") or "").strip().lower()
    meta = trigger.get("meta")
    if not isinstance(meta, dict):
        return None

    if action == "type":
        seq = meta.get("sequence")
        return seq if isinstance(seq, list) else None

    if action != "dispatch":
        return None
    if str(meta.get("action") or "").strip().lower() != "type":
        return None
    handler_meta = meta.get("handler_meta")
    if not isinstance(handler_meta, dict):
        return None
    seq = handler_meta.get("sequence")
    return seq if isinstance(seq, list) else None


def resolve_emission_targets(
    trigger: object, *, type_flag: bool, clipboard_flag: bool
) -> tuple[bool, bool]:
    """Decide whether to type and/or copy to clipboard.

    Verb destination, when set, overrides the caller's flags. With no
    destination, fall back to the flags but honor handler suppress_type.
    """
    destination = extract_destination(trigger)
    if destination == "clipboard":
        return False, True
    if destination == "type":
        return True, False
    if destination == "both":
        return True, True
    type_ = bool(type_flag) and not suppresses_type(trigger)
    return type_, bool(clipboard_flag)
