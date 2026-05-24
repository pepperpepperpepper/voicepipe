"""Prefix-based trigger matching against a transcribed string.

This is purely lexical — it looks for a configured trigger phrase at the
start of the transcript, then strips it (along with any separator
punctuation, spoken or otherwise). It does NOT do any audio wake-word
detection; that would happen earlier in the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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

    word_separators: tuple[tuple[str, str], ...] = (
        ("comma", ","),
        ("colon", ":"),
        ("semicolon", ";"),
        ("semi colon", ";"),
        ("period", "."),
        ("full stop", "."),
    )

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

        if not lowered.startswith(trigger):
            continue

        after = len(trigger)
        if after >= len(lowered):
            continue

        # Boundary-aware match: allow either whitespace or a separator after
        # the trigger. Prefer stripping a separator even when there's whitespace
        # before it (e.g. "zwingli , do it").
        i = after
        while i < len(lowered) and lowered[i].isspace():
            i += 1

        if i < len(lowered):
            # Trigger followed by a separator character.
            for sep in (",", ":", ";", "."):
                if lowered[i] == sep:
                    return TranscriptTriggerMatch(
                        trigger=trigger,
                        action=action,
                        remainder=cleaned[i + 1 :].lstrip(),
                        reason=f"prefix:{sep}",
                    )

            # Trigger followed by a separator word (e.g. "zwingli comma ...").
            for word, sep in word_separators:
                if not lowered.startswith(word, i):
                    continue
                end = i + len(word)
                if end < len(lowered):
                    next_ch = lowered[end]
                    if not (next_ch.isspace() or next_ch in {",", ":", ";", "."}):
                        continue
                j = end
                while j < len(lowered) and lowered[j].isspace():
                    j += 1
                if j < len(lowered) and lowered[j] in {",", ":", ";", "."}:
                    j += 1
                return TranscriptTriggerMatch(
                    trigger=trigger,
                    action=action,
                    remainder=cleaned[j:].lstrip(),
                    reason=f"prefix:{sep}",
                )

        # Trigger followed by whitespace and then non-separator content.
        if lowered[after].isspace():
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder=cleaned[after:].lstrip(),
                reason="prefix:space",
            )

    return None
