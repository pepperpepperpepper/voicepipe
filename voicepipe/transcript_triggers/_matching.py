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


# Fuzzy wake-word fallback tuning. STT renders "Zwangli" many ways (Zwang Li,
# Zwang Lee, Zwongli, Swangli…); rather than enumerate every spelling, accept a
# leading token within this edit distance of a configured trigger. Kept tight,
# and only applied to triggers at least this long, so ordinary opening words
# ("call", "open", "the") can't be mistaken for the wake word.
_FUZZY_MAX_DISTANCE = 2
_FUZZY_MIN_TRIGGER_LEN = 5


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings; no dependency)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (0 if ca == cb else 1),
                )
            )
        prev = cur
    return prev[-1]


def _remainder_after_tokens(cleaned: str, ntokens: int) -> str:
    rest = cleaned.split()[ntokens:]
    return " ".join(rest).lstrip(" ,:;.").strip()


def _fuzzy_wake_match(
    lowered: str, cleaned: str, triggers: Mapping[str, str]
) -> TranscriptTriggerMatch | None:
    tokens = lowered.split()
    if not tokens:
        return None
    # Compare the first token, and the first two tokens collapsed (so a spoken
    # "zwang li" → "zwangli"), against each trigger's space-collapsed form.
    heads: list[tuple[int, str]] = [(1, tokens[0])]
    if len(tokens) >= 2:
        heads.append((2, tokens[0] + tokens[1]))

    best: tuple[int, int, str, str] | None = None  # (dist, ntok, trigger, action)
    for raw_trigger, raw_action in triggers.items():
        trigger = (raw_trigger or "").strip().lower()
        collapsed = trigger.replace(" ", "")
        if len(collapsed) < _FUZZY_MIN_TRIGGER_LEN:
            continue
        action = (raw_action or "").strip().lower() or "strip"
        for ntok, head in heads:
            dist = _edit_distance(head, collapsed)
            if dist <= _FUZZY_MAX_DISTANCE and (best is None or dist < best[0]):
                best = (dist, ntok, trigger, action)
    if best is None:
        return None
    dist, ntok, trigger, action = best
    return TranscriptTriggerMatch(
        trigger=trigger,
        action=action,
        remainder=_remainder_after_tokens(cleaned, ntok),
        reason=f"fuzzy:{dist}",
    )


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

    # No exact/prefix hit — try a fuzzy wake-word match on the leading token(s).
    return _fuzzy_wake_match(lowered, cleaned, triggers)
