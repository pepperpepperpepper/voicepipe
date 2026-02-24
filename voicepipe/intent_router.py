from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

from voicepipe.transcription_result import TranscriptionResult


IntentMode = Literal["dictation", "command", "unknown"]


@dataclass(frozen=True)
class IntentResult:
    mode: IntentMode
    dictation_text: str | None = None
    command_text: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "dictation_text": self.dictation_text,
            "command_text": self.command_text,
            "reason": self.reason,
        }


DEFAULT_WAKE_PREFIXES: tuple[str, ...] = ("zwingli", "zwingly")


def route_intent(
    transcription: TranscriptionResult,
    *,
    wake_prefixes: Iterable[str] = DEFAULT_WAKE_PREFIXES,
) -> IntentResult:
    text = (transcription.text or "").strip()
    if not text:
        return IntentResult(mode="unknown", dictation_text="", reason="empty")

    lowered = text.lower()
    for raw_prefix in wake_prefixes:
        prefix = (raw_prefix or "").strip().lower()
        if not prefix:
            continue

        if lowered == prefix:
            return IntentResult(mode="command", command_text="", reason=f"prefix:{prefix}")

        if lowered.startswith(prefix + " "):
            stripped = text[len(prefix) :].lstrip()
            return IntentResult(mode="command", command_text=stripped, reason=f"prefix:{prefix}")

        if lowered.startswith(prefix + ","):
            stripped = text[len(prefix) + 1 :].lstrip()
            return IntentResult(mode="command", command_text=stripped, reason=f"prefix:{prefix}")

        if lowered.startswith(prefix + ":"):
            stripped = text[len(prefix) + 1 :].lstrip()
            return IntentResult(mode="command", command_text=stripped, reason=f"prefix:{prefix}")

    return IntentResult(mode="dictation", dictation_text=text, reason="default")
