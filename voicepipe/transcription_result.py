from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    backend: str
    model: str
    audio_file: str | None = None
    recording_id: str | None = None
    source: str | None = None
    transcript_trigger: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "text": self.text,
            "backend": self.backend,
            "model": self.model,
            "audio_file": self.audio_file,
            "recording_id": self.recording_id,
            "source": self.source,
            "warnings": list(self.warnings),
        }
        if self.transcript_trigger is not None:
            out["transcript_trigger"] = self.transcript_trigger
        return out


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
