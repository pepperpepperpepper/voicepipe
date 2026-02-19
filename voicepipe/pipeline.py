from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Iterable

from voicepipe.config import (
    get_intent_routing_enabled,
    get_intent_wake_prefixes,
    get_zwingli_backend,
    get_zwingli_base_url,
    get_zwingli_model,
)
from voicepipe.intent_router import IntentResult, route_intent
from voicepipe.transcription_result import TranscriptionResult
from voicepipe.zwingli import process_zwingli_prompt_result


PipelineStage = str


@dataclass(frozen=True)
class PostprocessResult:
    ok: bool
    stage: PipelineStage
    error: str | None
    transcription: TranscriptionResult
    intent: IntentResult
    output_text: str | None
    zwingli: dict[str, Any] | None
    timing: dict[str, int] | None

    def to_payload(self) -> dict[str, Any]:
        payload = self.transcription.to_dict()
        payload["ok"] = bool(self.ok)
        payload["stage"] = self.stage
        payload["error"] = self.error
        payload["intent"] = self.intent.to_dict()
        payload["output_text"] = self.output_text
        if self.zwingli is not None:
            payload["zwingli"] = self.zwingli
        if self.timing is not None:
            payload["timing"] = self.timing
        return payload


def _strict_commands_enabled() -> bool:
    return (os.environ.get("VOICEPIPE_COMMANDS_STRICT") or "").strip() == "1"


def postprocess_transcription(
    transcription: TranscriptionResult,
    *,
    intent_routing_enabled: bool | None = None,
    wake_prefixes: Iterable[str] | None = None,
    strict_commands: bool | None = None,
) -> PostprocessResult:
    """Apply intent routing and zwingli post-processing to a transcription.

    This function intentionally does not perform any typing or printing.
    """

    started = time.monotonic()
    routing_enabled = (
        get_intent_routing_enabled() if intent_routing_enabled is None else intent_routing_enabled
    )
    if routing_enabled:
        prefixes = get_intent_wake_prefixes() if wake_prefixes is None else list(wake_prefixes)
        intent = route_intent(transcription, wake_prefixes=prefixes)
    else:
        intent = IntentResult(mode="dictation", dictation_text=transcription.text, reason="disabled")

    intent_ms = int((time.monotonic() - started) * 1000)

    strict = _strict_commands_enabled() if strict_commands is None else strict_commands
    if strict and routing_enabled and intent.mode == "command":
        msg = "Zwingli-mode detected but VOICEPIPE_COMMANDS_STRICT=1; refusing to output."
        zwingli_meta: dict[str, Any] = {
            "backend": get_zwingli_backend(),
            "model": get_zwingli_model(),
            "error": msg,
        }
        base_url = get_zwingli_base_url()
        if base_url:
            zwingli_meta["provider"] = {"base_url": base_url}
        return PostprocessResult(
            ok=False,
            stage="strict",
            error=msg,
            transcription=transcription,
            intent=intent,
            output_text=None,
            zwingli=zwingli_meta,
            timing={"intent_ms": intent_ms},
        )

    if intent.mode == "command":
        zwingli_backend = get_zwingli_backend()
        zwingli_model = get_zwingli_model()
        zwingli_base_url = get_zwingli_base_url()
        try:
            output_text, meta = process_zwingli_prompt_result(intent.command_text or "")
            timing = {"intent_ms": intent_ms}
            if isinstance(meta, dict) and isinstance(meta.get("duration_ms"), int):
                timing["zwingli_ms"] = int(meta["duration_ms"])
            return PostprocessResult(
                ok=True,
                stage="complete",
                error=None,
                transcription=transcription,
                intent=intent,
                output_text=output_text,
                zwingli=meta if isinstance(meta, dict) else None,
                timing=timing,
            )
        except Exception as e:
            msg = str(e)
            zwingli_meta: dict[str, Any] = {"backend": zwingli_backend, "model": zwingli_model, "error": msg}
            if zwingli_base_url:
                zwingli_meta["provider"] = {"base_url": zwingli_base_url}
            return PostprocessResult(
                ok=False,
                stage="zwingli",
                error=msg,
                transcription=transcription,
                intent=intent,
                output_text=None,
                zwingli=zwingli_meta,
                timing={"intent_ms": intent_ms},
            )

    if intent.mode == "dictation":
        return PostprocessResult(
            ok=True,
            stage="complete",
            error=None,
            transcription=transcription,
            intent=intent,
            output_text=intent.dictation_text if intent.dictation_text is not None else transcription.text,
            zwingli=None,
            timing={"intent_ms": intent_ms},
        )

    return PostprocessResult(
        ok=True,
        stage="complete",
        error=None,
        transcription=transcription,
        intent=intent,
        output_text=transcription.text,
        zwingli=None,
        timing={"intent_ms": intent_ms},
    )


def build_error_payload(
    *,
    stage: PipelineStage,
    error: str,
    transcription: TranscriptionResult | None = None,
    audio_file: str | None = None,
    recording_id: str | None = None,
    source: str | None = None,
    model: str | None = None,
    backend: str | None = None,
    intent: IntentResult | None = None,
    output_text: str | None = None,
    zwingli: dict[str, Any] | None = None,
    timing: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build a structured JSON payload for failure modes.

    This keeps `--json` / integration outputs stable even when failures happen
    before a full `TranscriptionResult` is available.
    """

    base = (
        transcription.to_dict()
        if transcription is not None
        else {
            "text": None,
            "backend": backend,
            "model": model,
            "audio_file": audio_file,
            "recording_id": recording_id,
            "source": source,
            "warnings": [],
        }
    )

    payload: dict[str, Any] = dict(base)
    payload["ok"] = False
    payload["stage"] = stage
    payload["error"] = error
    payload["intent"] = (
        intent.to_dict()
        if intent is not None
        else IntentResult(mode="unknown", dictation_text=None, reason="error").to_dict()
    )
    payload["output_text"] = output_text
    if zwingli is not None:
        payload["zwingli"] = zwingli
    if timing is not None:
        payload["timing"] = timing
    return payload
