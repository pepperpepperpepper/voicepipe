"""Trivial action handlers that don't need a dedicated module."""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)


def _action_strip(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    return (prompt or "").strip(), {}


def _action_clipboard(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    """Passthrough handler: the actual clipboard copy is performed by the
    emission layer via verb destination routing (see verb_cfg.destination)."""
    del verb_cfg, profiles, captures, commands
    return (prompt or "").strip(), {}
