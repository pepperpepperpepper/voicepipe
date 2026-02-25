from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
