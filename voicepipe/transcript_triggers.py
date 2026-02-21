from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from voicepipe.config import get_transcript_triggers


@dataclass(frozen=True)
class TranscriptTriggerMatch:
    trigger: str
    action: str
    remainder: str
    reason: str


def match_transcript_trigger(
    text: str,
    *,
    triggers: Mapping[str, str],
) -> TranscriptTriggerMatch | None:
    """Match a configured trigger prefix against transcript text.

    This is intentionally lightweight (string checks only). It is not an audio
    wake word; it operates purely on the transcription output.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    lowered = cleaned.lower()

    for raw_trigger, raw_action in triggers.items():
        trigger = (raw_trigger or "").strip().lower()
        if not trigger:
            continue
        action = (raw_action or "").strip().lower() or "strip"

        if lowered == trigger:
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder="",
                reason="exact",
            )

        if lowered.startswith(trigger + " "):
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder=cleaned[len(trigger) :].lstrip(),
                reason="prefix:space",
            )

        for sep in (",", ":", ";", "."):
            if lowered.startswith(trigger + sep):
                return TranscriptTriggerMatch(
                    trigger=trigger,
                    action=action,
                    remainder=cleaned[len(trigger) + 1 :].lstrip(),
                    reason=f"prefix:{sep}",
                )

    return None


def _action_strip(prompt: str) -> tuple[str, dict[str, Any]]:
    return (prompt or "").strip(), {}


def _action_zwingli(prompt: str) -> tuple[str, dict[str, Any]]:
    from voicepipe.zwingli import process_zwingli_prompt_result

    text, meta = process_zwingli_prompt_result(prompt)
    if not isinstance(meta, dict):
        meta = {"meta": meta}
    return text, meta


_ACTIONS: dict[str, Callable[[str], tuple[str, dict[str, Any]]]] = {
    "strip": _action_strip,
    "zwingli": _action_zwingli,
}


def apply_transcript_triggers(
    text: str,
    *,
    triggers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Apply a configured transcript trigger, returning (output_text, metadata).

    If no trigger matches, this returns the original text and `None` metadata.
    """
    resolved_triggers = get_transcript_triggers(load_env=False) if triggers is None else dict(triggers)
    match = match_transcript_trigger(text, triggers=resolved_triggers)
    if match is None:
        return text, None

    handler = _ACTIONS.get(match.action)
    if handler is None:
        return match.remainder, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": f"Unknown transcript trigger action: {match.action!r}",
        }

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
        return output_text, payload
    except Exception as e:
        return match.remainder, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": str(e),
        }

