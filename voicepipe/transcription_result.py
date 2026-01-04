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
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "backend": self.backend,
            "model": self.model,
            "audio_file": self.audio_file,
            "recording_id": self.recording_id,
            "source": self.source,
            "warnings": list(self.warnings),
        }

