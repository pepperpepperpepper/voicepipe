"""Transcript-trigger / Zwingli dispatch.

Public API:
    apply_transcript_triggers(text, *, commands=None, triggers=None)
        Match a configured trigger prefix against `text` and (if matched)
        dispatch the remainder through the verb pipeline. Returns
        ``(output_text, metadata_or_None)``. The single entry point used by
        the recording/typing paths.

    match_transcript_trigger(text, *, triggers)
        Low-level: returns a TranscriptTriggerMatch (or None) for prefix
        matching only. Used by the recording path before transcription is
        complete.

    dry_run_dispatch(text, *, commands)
        Side-effect-free analyzer. Walks the same code paths as
        ``apply_transcript_triggers`` but never calls handlers, the LLM,
        the shell, the rate limiter, or the pending store. Used by
        ``voicepipe triggers test``.

This module used to be a single 2200-line file; it's now a package whose
submodules group related logic (matching, dispatch, action handlers,
template/pattern utilities, debug log, error routing). The package
re-exports a stable set of symbols so existing callers and tests don't
need to know the internal layout.
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    get_transcript_triggers,
    get_transcript_commands_config,
)

# Public API.
from ._matching import TranscriptTriggerMatch, match_transcript_trigger
from ._dry_run import dry_run_dispatch

# Internal symbols re-exported for tests/CLI that reach for them by name.
# Keep this list in sync with what's referenced via `tt.<name>` in tests.
from ._actions import (
    _ACTIONS,
    _action_type,
    _run_shell_command,
    subprocess,  # for `monkeypatch.setattr(tt.subprocess, "run", ...)`
)
from ._template import (
    _compile_verb_pattern,
    _find_pattern_match,
    _render_user_prompt_template,
)
from ._errors import _format_zwingli_error_text
from ._debug_log import (
    _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES,
    _maybe_rotate_debug_log,
    _zwingli_debug_log_max_bytes,
)

# Submodules used internally by the orchestrator below.
from ._dispatch import _default_commands_for_triggers, _dispatch_prompt
from ._debug_log import _write_zwingli_debug_event
from ._errors import _apply_error_destination


__all__ = [
    "TranscriptTriggerMatch",
    "match_transcript_trigger",
    "dry_run_dispatch",
    "apply_transcript_triggers",
]


def _maybe_play_audio_feedback(payload: dict[str, Any]) -> None:
    """Fire the audio cue (if any) for an apply_transcript_triggers payload.

    Best-effort: audio feedback must never block or break the text-output
    path, so every failure is swallowed silently.
    """
    try:
        from voicepipe import audio_feedback

        event = audio_feedback.event_for_trigger_payload(payload)
        if event:
            audio_feedback.play(event)
    except Exception:
        pass


def apply_transcript_triggers(
    text: str,
    *,
    commands: TranscriptCommandsConfig | None = None,
    triggers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Apply a configured transcript trigger, returning (output_text, metadata).

    If no trigger matches, this returns the original text and `None` metadata.
    """
    resolved_triggers: Mapping[str, str]
    resolved_commands: TranscriptCommandsConfig | None = None

    if commands is not None:
        resolved_commands = commands
        resolved_triggers = commands.triggers
    elif triggers is not None:
        resolved_triggers = triggers
    else:
        # Lightweight hot path: load trigger prefixes only. Full verbs/profiles
        # config is loaded lazily only if a trigger matches and requests it
        # (e.g. action=dispatch).
        resolved_triggers = get_transcript_triggers(load_env=False)

    match = match_transcript_trigger(text, triggers=resolved_triggers)
    if match is None:
        return text, None

    _write_zwingli_debug_event(
        {
            "event": "trigger_match",
            "text": (text or ""),
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "remainder": match.remainder,
        }
    )

    if match.action == "dispatch":
        if resolved_commands is None:
            if triggers is not None:
                resolved_commands = _default_commands_for_triggers(resolved_triggers)
            else:
                resolved_commands = get_transcript_commands_config(load_env=False)

        try:
            output_text, meta = _dispatch_prompt(
                match.remainder,
                commands=resolved_commands,
            )
            payload: dict[str, Any] = {
                "ok": True,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
            }
            if meta:
                payload["meta"] = meta
            _write_zwingli_debug_event(
                {
                    "event": "dispatch_ok",
                    "trigger": match.trigger,
                    "reason": match.reason,
                    "remainder": match.remainder,
                    "output_text": output_text,
                    "meta": meta,
                }
            )
            _maybe_play_audio_feedback(payload)
            return output_text, payload
        except Exception as e:
            _write_zwingli_debug_event(
                {
                    "event": "dispatch_error",
                    "trigger": match.trigger,
                    "reason": match.reason,
                    "remainder": match.remainder,
                    "error": str(e),
                }
            )
            error_text, error_meta = _apply_error_destination(
                str(e), commands=resolved_commands
            )
            error_payload = {
                "ok": False,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "error": str(e),
                "meta": error_meta,
            }
            _maybe_play_audio_feedback(error_payload)
            return error_text, error_payload

    handler = _ACTIONS.get(match.action)
    if handler is None:
        _write_zwingli_debug_event(
            {
                "event": "action_missing",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
            }
        )
        error_msg = f"Unknown transcript trigger action: {match.action!r}"
        error_text, error_meta = _apply_error_destination(error_msg, commands=resolved_commands)
        error_payload = {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": error_msg,
            "meta": error_meta,
        }
        _maybe_play_audio_feedback(error_payload)
        return error_text, error_payload

    try:
        output_text, meta = handler(match.remainder)
        payload: dict[str, Any] = {
            "ok": True,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
        }
        if meta:
            payload["meta"] = meta
        _write_zwingli_debug_event(
            {
                "event": "action_ok",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
                "output_text": output_text,
                "meta": meta,
            }
        )
        _maybe_play_audio_feedback(payload)
        return output_text, payload
    except Exception as e:
        _write_zwingli_debug_event(
            {
                "event": "action_error",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
                "error": str(e),
            }
        )
        error_text, error_meta = _apply_error_destination(str(e), commands=resolved_commands)
        error_payload = {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": str(e),
            "meta": error_meta,
        }
        _maybe_play_audio_feedback(error_payload)
        return error_text, error_payload
