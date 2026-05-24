"""``help`` action: auto-injected verb that describes the loaded config.

``zwingli help`` (no args) prints the active backend, model, and a
one-line summary per verb. ``zwingli help <verb>`` prints a verb's
full config block. The describers are also used by other tooling (e.g.
the dry-run trace prefers the same shape).
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)


def _describe_verb_one_line(verb: str, cfg: TranscriptVerbConfig) -> str:
    parts = [verb]
    type_label = (cfg.type or "").strip().lower()
    action_label = (cfg.action or "").strip().lower()
    if type_label == "llm" and cfg.profile:
        parts.append(f"(llm:{cfg.profile})")
    elif type_label and type_label != action_label:
        parts.append(f"({type_label}:{action_label})")
    elif action_label:
        parts.append(f"({action_label})")
    effective_destination = cfg.destination
    if not effective_destination and action_label == "clipboard":
        effective_destination = "clipboard"
    if effective_destination:
        parts.append(f"-> {effective_destination}")
    if cfg.aliases:
        parts.append("aliases: " + ", ".join(cfg.aliases))
    if not cfg.enabled:
        parts.append("[disabled]")
    return "  " + " ".join(parts)


def _describe_verb_full(verb: str, cfg: TranscriptVerbConfig) -> str:
    lines = [f"{verb}:"]
    lines.append(f"  type: {cfg.type or '(unset)'}")
    lines.append(f"  action: {cfg.action or '(unset)'}")
    lines.append(f"  enabled: {cfg.enabled}")
    effective_destination = cfg.destination
    if not effective_destination and (cfg.action or "").strip().lower() == "clipboard":
        effective_destination = "clipboard"
    if effective_destination:
        lines.append(f"  destination: {effective_destination}")
    if cfg.profile:
        lines.append(f"  profile: {cfg.profile}")
    if cfg.timeout_seconds is not None:
        lines.append(f"  timeout_seconds: {cfg.timeout_seconds}")
    if cfg.aliases:
        lines.append(f"  aliases: {', '.join(cfg.aliases)}")
    if cfg.pattern:
        lines.append(f"  pattern: {cfg.pattern}")
    if cfg.command_template:
        lines.append(f"  command_template: {cfg.command_template}")
    if cfg.interpreter:
        lines.append(f"  interpreter: {cfg.interpreter}")
    if cfg.confirm:
        lines.append(f"  confirm: true")
        if cfg.confirm_timeout_seconds is not None:
            lines.append(f"  confirm_timeout_seconds: {cfg.confirm_timeout_seconds}")
    if cfg.rate_limit_per_min is not None:
        lines.append(f"  rate_limit_per_min: {cfg.rate_limit_per_min}")
    if cfg.plugin is not None:
        src = cfg.plugin.module or cfg.plugin.path or "(unset)"
        lines.append(f"  plugin: {src}::{cfg.plugin.callable or '(unset)'}")
    return "\n".join(lines)


def _action_help(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures

    args = (prompt or "").strip().lower()
    verbs = dict(commands.verbs) if commands else {}

    if args:
        # Resolve aliases to canonical verb.
        target = args
        if target not in verbs:
            for name, cfg in verbs.items():
                if args in (a.lower() for a in (cfg.aliases or ())):
                    target = name
                    break
        if target in verbs:
            return _describe_verb_full(target, verbs[target]), {"help_target": target}
        known = ", ".join(sorted(verbs)) or "(none)"
        return (
            f"voicepipe help: unknown verb {args!r}.\nKnown verbs: {known}",
            {"help_target": args, "help_unknown": True},
        )

    backend = os.environ.get("VOICEPIPE_TRANSCRIBE_BACKEND") or "openai"
    model = os.environ.get("VOICEPIPE_TRANSCRIBE_MODEL") or os.environ.get(
        "VOICEPIPE_MODEL"
    ) or "(default)"
    lines = [f"voicepipe — backend: {backend}, model: {model}", ""]
    if verbs:
        lines.append("Verbs:")
        for name in sorted(verbs):
            lines.append(_describe_verb_one_line(name, verbs[name]))
    else:
        lines.append("Verbs: (none configured)")
    lines.append("")
    lines.append("Say 'zwingli help <verb>' for details on a specific verb.")
    return "\n".join(lines), {"help_target": None}
