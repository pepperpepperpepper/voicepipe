"""Format and route Zwingli error messages.

When dispatch raises or the trigger action is missing, we format the
reason as a ``⚠ zwingli: …`` line and decide whether to type, copy, or
do both, per ``dispatch.error_destination``. The caller's typing/clipboard
flow then sees the same shape it gets from a normal verb output.
"""

from __future__ import annotations

from typing import Any

from voicepipe.config import (
    TranscriptCommandsConfig,
    get_transcript_commands_config,
)


_ZWINGLI_ERROR_PREFIX = "⚠ zwingli"
_ERROR_DESTINATION_FALLBACK = "type"
_ERROR_DESTINATION_VALID = frozenset({"type", "clipboard", "both"})


def _format_zwingli_error_text(reason: str) -> str:
    cleaned = (reason or "").strip()
    return f"{_ZWINGLI_ERROR_PREFIX}: {cleaned}" if cleaned else f"{_ZWINGLI_ERROR_PREFIX} error"


def _resolve_error_destination(commands: TranscriptCommandsConfig | None) -> str:
    """Read dispatch.error_destination; fall back to 'type' on any issue."""
    if commands is None:
        try:
            commands = get_transcript_commands_config(load_env=False)
        except Exception:
            return _ERROR_DESTINATION_FALLBACK
    raw = (getattr(commands.dispatch, "error_destination", None) or _ERROR_DESTINATION_FALLBACK)
    cleaned = raw.strip().lower() if isinstance(raw, str) else _ERROR_DESTINATION_FALLBACK
    return cleaned if cleaned in _ERROR_DESTINATION_VALID else _ERROR_DESTINATION_FALLBACK


def _apply_error_destination(
    reason: str, *, commands: TranscriptCommandsConfig | None
) -> tuple[str, dict[str, Any]]:
    """Format the error and route it per dispatch.error_destination.

    Returns (output_text, meta_extras). The output_text is what callers should
    type/echo; meta_extras carries suppress_type and clipboard flags so the
    same downstream wiring used by the clipboard verb picks this up.
    """
    error_text = _format_zwingli_error_text(reason)
    destination = _resolve_error_destination(commands)
    extras: dict[str, Any] = {"error_destination": destination}

    if destination in ("clipboard", "both"):
        try:
            from voicepipe.clipboard import copy_to_clipboard

            ok, _err = copy_to_clipboard(error_text)
            extras["clipboard"] = bool(ok)
        except Exception:
            extras["clipboard"] = False

    if destination == "clipboard":
        extras["suppress_type"] = True

    return error_text, extras
