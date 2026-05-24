"""Side-effect-free dispatch tracer for ``voicepipe triggers test``.

Mirrors :func:`_dispatch_prompt` step-by-step but builds a structured
trace dict instead of calling any handler. Used by the CLI to show what
*would* happen for a given spoken phrase without touching the LLM, the
shell, the rate-limit windows, the pending store, or the audio cues.
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._dispatch import _resolve_verb_and_args, _split_chain_chunks
from ._matching import match_transcript_trigger
from ._template import (
    _find_pattern_match,
    _render_user_prompt_template,
    _substitute_command_template,
)


def _dry_run_verb_summary(
    verb_cfg: TranscriptVerbConfig,
    *,
    commands: TranscriptCommandsConfig,
    args: str,
    captures: Mapping[str, str] | None,
) -> dict[str, Any]:
    """Build a side-effect-free summary of what a verb *would* do."""
    summary: dict[str, Any] = {
        "type": verb_cfg.type,
        "action": verb_cfg.action,
    }
    if verb_cfg.profile:
        summary["profile"] = verb_cfg.profile
    if verb_cfg.interpreter:
        summary["interpreter"] = verb_cfg.interpreter
    if verb_cfg.timeout_seconds is not None:
        summary["timeout_seconds"] = verb_cfg.timeout_seconds

    destination = verb_cfg.destination
    if not destination and (verb_cfg.action or "").strip().lower() == "clipboard":
        destination = "clipboard"
    if destination:
        summary["destination"] = destination
    else:
        summary["destination"] = "(inherit from caller flags)"

    if verb_cfg.confirm:
        summary["confirm"] = True
        if verb_cfg.confirm_timeout_seconds is not None:
            summary["confirm_timeout_seconds"] = verb_cfg.confirm_timeout_seconds
    if verb_cfg.rate_limit_per_min is not None:
        summary["rate_limit_per_min"] = verb_cfg.rate_limit_per_min

    if verb_cfg.command_template:
        summary["command_template"] = verb_cfg.command_template
        if captures:
            summary["rendered_command"] = _substitute_command_template(
                verb_cfg.command_template, captures
            )

    action = (verb_cfg.action or "").strip().lower()

    if action in ("zwingli", "codegen"):
        profile_name = (verb_cfg.profile or "").strip().lower()
        profile: TranscriptLLMProfileConfig | None = None
        if profile_name:
            profile = commands.llm_profiles.get(profile_name)
        if profile is None and profile_name:
            summary["llm_profile_missing"] = profile_name
        elif profile is not None:
            user_prompt = args
            if profile.user_prompt_template:
                user_prompt = _render_user_prompt_template(
                    profile.user_prompt_template, text=args, captures=captures
                )
            summary["llm_preview"] = {
                "model": profile.model or "(zwingli default)",
                "temperature": (
                    profile.temperature
                    if profile.temperature is not None
                    else "(zwingli default)"
                ),
                "system_prompt": profile.system_prompt or "(none)",
                "user_prompt": user_prompt,
            }

    if action == "shell":
        cmd = (
            _substitute_command_template(verb_cfg.command_template, captures)
            if verb_cfg.command_template and captures
            else args
        )
        summary["would_run_shell"] = cmd
        if verb_cfg.confirm:
            summary["would_stash_pending"] = True
    elif action == "execute":
        cmd = (
            _substitute_command_template(verb_cfg.command_template, captures)
            if verb_cfg.command_template and captures
            else args
        )
        summary["would_type"] = cmd
        summary["would_press_enter"] = True
        if verb_cfg.confirm:
            summary["would_stash_pending"] = True

    return summary


def _dry_run_dispatch_step(
    chunk: str, *, commands: TranscriptCommandsConfig, args_override: str | None = None
) -> dict[str, Any]:
    """Trace one dispatch step. ``args_override`` is set for chain steps that
    pipe the previous step's output in instead of resolving args from the chunk."""
    step: dict[str, Any] = {"chunk": chunk}

    pattern_result = _find_pattern_match(chunk, commands=commands)
    if pattern_result is not None:
        pattern_verb, captures = pattern_result
        verb_cfg = commands.verbs[pattern_verb]
        step["resolution"] = "pattern"
        step["verb"] = pattern_verb
        step["captures"] = dict(captures)
        step["args"] = chunk
        step["verb_config"] = _dry_run_verb_summary(
            verb_cfg, commands=commands, args=chunk, captures=captures
        )
        return step

    if args_override is not None:
        verb, _ = _resolve_verb_and_args(chunk, commands=commands)
        resolved_args = args_override
        step["piped_from_previous"] = True
    else:
        verb, resolved_args = _resolve_verb_and_args(chunk, commands=commands)

    step["args"] = resolved_args
    if not verb:
        step["resolution"] = "no_verb"
        step["fallback_action"] = (
            commands.dispatch.unknown_verb or "strip"
        ).strip().lower() or "strip"
        return step

    verb_cfg = commands.verbs.get(verb)
    if verb_cfg is None:
        step["resolution"] = "unknown_verb"
        step["verb"] = verb
        step["fallback_action"] = (
            commands.dispatch.unknown_verb or "strip"
        ).strip().lower() or "strip"
        return step

    if not bool(verb_cfg.enabled):
        step["resolution"] = "disabled_verb"
        step["verb"] = verb
        step["fallback_action"] = (
            commands.dispatch.unknown_verb or "strip"
        ).strip().lower() or "strip"
        return step

    step["resolution"] = "verb"
    step["verb"] = verb
    step["verb_config"] = _dry_run_verb_summary(
        verb_cfg, commands=commands, args=resolved_args, captures=None
    )
    return step


def dry_run_dispatch(
    text: str, *, commands: TranscriptCommandsConfig
) -> dict[str, Any]:
    """Walk the trigger/dispatch logic without side effects.

    Returns a structured trace dict suitable for human or JSON output.
    Does not call action handlers, the LLM, subprocess, the clipboard, the
    rate-limit windows, or the pending-command store. Useful for the
    ``voicepipe triggers test`` CLI and for unit tests that want to inspect
    resolution decisions without executing them.
    """
    trace: dict[str, Any] = {"input": text}

    match = match_transcript_trigger(text, triggers=commands.triggers)
    if match is None:
        trace["trigger_match"] = None
        trace["outcome"] = "no_trigger_matched"
        return trace

    trace["trigger_match"] = {
        "trigger": match.trigger,
        "action": match.action,
        "remainder": match.remainder,
        "reason": match.reason,
    }

    if match.action != "dispatch":
        trace["outcome"] = "trigger_action"
        trace["trigger_action"] = match.action
        return trace

    chunks = _split_chain_chunks(match.remainder, commands=commands)
    trace["chain_length"] = len(chunks)

    steps: list[dict[str, Any]] = []
    prior_resolved_for_pipe: bool = False
    for i, chunk in enumerate(chunks):
        if i == 0:
            step = _dry_run_dispatch_step(chunk, commands=commands)
        else:
            _, split_args = _resolve_verb_and_args(chunk, commands=commands)
            if split_args.strip():
                step = _dry_run_dispatch_step(chunk, commands=commands)
            else:
                # Verb-only chain step: would receive previous step's output.
                step = _dry_run_dispatch_step(
                    chunk,
                    commands=commands,
                    args_override="(piped from previous step's output)",
                )
                prior_resolved_for_pipe = True
        step["step_index"] = i
        steps.append(step)
    trace["steps"] = steps
    if prior_resolved_for_pipe:
        trace["chain_uses_pipe"] = True

    return trace
