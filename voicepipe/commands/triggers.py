"""`voicepipe triggers …` commands."""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import click

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptDispatchConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
    VoicepipeConfigError,
    triggers_json_path,
    validate_triggers_json,
)


def _load_config_or_exit(
    path_override: str | None,
) -> tuple[
    dict[str, str],
    TranscriptDispatchConfig,
    dict[str, TranscriptVerbConfig],
    dict[str, TranscriptLLMProfileConfig],
]:
    """Load triggers.json and exit with a friendly message on common failures."""
    path = Path(path_override).expanduser() if path_override else triggers_json_path()
    try:
        return validate_triggers_json(path=path)
    except FileNotFoundError:
        click.echo(f"✗ triggers.json not found: {path}", err=True)
        sys.exit(1)
    except VoicepipeConfigError as e:
        click.echo(f"✗ triggers.json invalid: {path}", err=True)
        click.echo(f"  {e}", err=True)
        sys.exit(1)


@click.group(name="triggers")
def triggers_group() -> None:
    """Inspect and manage the Zwingli triggers config."""


def _format_summary(
    triggers: dict[str, str],
    verbs: dict[str, TranscriptVerbConfig],
    profiles: dict[str, TranscriptLLMProfileConfig],
) -> list[str]:
    return [
        f"  {len(triggers)} trigger{'s' if len(triggers) != 1 else ''}: "
        + (", ".join(sorted(triggers)) if triggers else "(none)"),
        f"  {len(verbs)} verb{'s' if len(verbs) != 1 else ''}: "
        + (", ".join(sorted(verbs)) if verbs else "(none)"),
        f"  {len(profiles)} llm profile{'s' if len(profiles) != 1 else ''}: "
        + (", ".join(sorted(profiles)) if profiles else "(none)"),
    ]


def _collect_strict_warnings(
    verbs: dict[str, TranscriptVerbConfig],
    profiles: dict[str, TranscriptLLMProfileConfig],
) -> list[str]:
    """Return a list of non-fatal warnings about likely configuration mistakes.

    These don't make triggers.json invalid (it loads fine), but they're
    things the user probably wants to know about: dangling profile
    references, codegen verbs whose interpreter isn't installed, alias
    collisions across verbs.
    """
    warnings: list[str] = []

    # Profile references that don't resolve.
    for verb_name, cfg in sorted(verbs.items()):
        profile = (cfg.profile or "").strip()
        if profile and profile not in profiles:
            warnings.append(
                f"verb {verb_name!r}: profile {profile!r} is not defined in llm_profiles"
            )

    # Codegen interpreters not on PATH.
    for verb_name, cfg in sorted(verbs.items()):
        if (cfg.type or "").strip().lower() != "codegen":
            continue
        interpreter = (cfg.interpreter or "").strip()
        if not interpreter:
            continue
        if shutil.which(interpreter) is None:
            warnings.append(
                f"verb {verb_name!r}: interpreter {interpreter!r} not found in PATH"
            )

    # Alias collisions: first verb to claim a phrase wins, so any subsequent
    # claimant is silently shadowed.
    seen_aliases: dict[str, str] = {}
    for verb_name, cfg in verbs.items():
        for alias in cfg.aliases or ():
            phrase = " ".join((alias or "").strip().lower().split())
            if not phrase:
                continue
            if phrase in verbs and phrase != verb_name:
                warnings.append(
                    f"alias {phrase!r} (on verb {verb_name!r}) shadows a verb of the same name"
                )
                continue
            if phrase in seen_aliases and seen_aliases[phrase] != verb_name:
                warnings.append(
                    f"alias {phrase!r} is claimed by both {seen_aliases[phrase]!r} "
                    f"and {verb_name!r}; the first wins"
                )
            else:
                seen_aliases.setdefault(phrase, verb_name)

    return warnings


@triggers_group.command("validate")
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to a triggers.json file (defaults to the canonical path).",
)
@click.option(
    "--strict",
    is_flag=True,
    help=(
        "Also report non-fatal warnings: dangling profile references, "
        "codegen interpreters not in PATH, alias collisions."
    ),
)
def triggers_validate(path_override: str | None, strict: bool) -> None:
    """Validate the triggers.json file and print a summary."""
    path = Path(path_override).expanduser() if path_override else triggers_json_path()
    triggers, dispatch, verbs, profiles = _load_config_or_exit(path_override)

    click.echo(f"✓ triggers.json valid: {path}")
    for line in _format_summary(triggers, verbs, profiles):
        click.echo(line)

    if strict:
        warnings = _collect_strict_warnings(verbs, profiles)
        if warnings:
            click.echo("")
            click.echo(f"warnings ({len(warnings)}):")
            for w in warnings:
                click.echo(f"  - {w}")
            sys.exit(2)


def _format_dry_run_trace(trace: dict[str, Any]) -> list[str]:
    """Render a dry-run trace dict as indented human-readable lines."""
    lines: list[str] = []
    lines.append(f"Input: {trace['input']!r}")
    lines.append("")

    match = trace.get("trigger_match")
    if match is None:
        lines.append("No trigger matched — text would be typed as-is.")
        return lines

    lines.append("Trigger matched:")
    lines.append(f"  trigger:   {match['trigger']}")
    lines.append(f"  action:    {match['action']}")
    lines.append(f"  remainder: {match['remainder']!r}")
    lines.append(f"  reason:    {match['reason']}")
    lines.append("")

    if trace.get("outcome") == "trigger_action":
        lines.append(f"Trigger action: {trace['trigger_action']}")
        lines.append("(no dispatch; the remainder is returned unchanged)")
        return lines

    chain_length = trace.get("chain_length", 1)
    header = (
        f"Dispatch (1 step):"
        if chain_length == 1
        else f"Dispatch (chain of {chain_length} steps):"
    )
    lines.append(header)
    for step in trace.get("steps") or ():
        lines.append("")
        lines.extend(_format_dry_run_step(step))
    return lines


def _format_dry_run_step(step: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lines.append(f"  Step {step['step_index'] + 1}")
    lines.append(f"    chunk:      {step['chunk']!r}")
    if step.get("piped_from_previous"):
        lines.append("    input:      (piped from previous step's output)")
    resolution = step.get("resolution") or "?"
    lines.append(f"    resolution: {resolution}")
    if "verb" in step:
        lines.append(f"    verb:       {step['verb']}")
    if "args" in step and not step.get("piped_from_previous"):
        lines.append(f"    args:       {step['args']!r}")
    if "captures" in step:
        for name, value in step["captures"].items():
            lines.append(f"    capture {name}: {value!r}")
    if "fallback_action" in step:
        lines.append(f"    fallback:   {step['fallback_action']}")

    cfg = step.get("verb_config")
    if cfg:
        lines.append("    config:")
        ordered_keys = (
            "type",
            "action",
            "profile",
            "interpreter",
            "destination",
            "timeout_seconds",
            "confirm",
            "confirm_timeout_seconds",
            "rate_limit_per_min",
            "command_template",
            "rendered_command",
            "llm_profile_missing",
            "would_run_shell",
            "would_type",
            "would_press_enter",
            "would_stash_pending",
        )
        for key in ordered_keys:
            if key in cfg:
                lines.append(f"      {key}: {cfg[key]}")

        llm = cfg.get("llm_preview")
        if llm:
            lines.append("    LLM call (would be sent):")
            lines.append(f"      model:       {llm['model']}")
            lines.append(f"      temperature: {llm['temperature']}")
            lines.append(f"      system:      {_indent_multiline(llm['system_prompt'], 19)}")
            lines.append(f"      user:        {_indent_multiline(llm['user_prompt'], 19)}")
    return lines


def _indent_multiline(text: str, indent: int) -> str:
    """Re-indent continuation lines so multi-line prompts stay readable."""
    if not isinstance(text, str) or "\n" not in text:
        return text
    pad = " " * indent
    head, *tail = text.split("\n")
    return head + "\n" + "\n".join(pad + line for line in tail)


@triggers_group.command("test")
@click.argument("phrase", required=True)
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to a triggers.json file (defaults to the canonical path).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the trace as JSON instead of formatted text.",
)
def triggers_test(phrase: str, path_override: str | None, json_output: bool) -> None:
    """Dry-run a spoken PHRASE through dispatch without executing anything.

    Shows the trigger match, chain split, per-step verb resolution
    (including alias/pattern matches), the verb's effective config, and
    the LLM call that would be sent. Does not call the LLM, run shell
    commands, type into windows, touch the clipboard, increment rate
    limits, or stash pending state.
    """
    from voicepipe.transcript_triggers import dry_run_dispatch

    triggers, dispatch, verbs, profiles = _load_config_or_exit(path_override)

    commands = TranscriptCommandsConfig(
        triggers=triggers,
        dispatch=dispatch,
        verbs=verbs,
        llm_profiles=profiles,
    )
    trace = dry_run_dispatch(phrase, commands=commands)

    if json_output:
        click.echo(json.dumps(trace, indent=2, ensure_ascii=False))
        return
    for line in _format_dry_run_trace(trace):
        click.echo(line)


# ---------- triggers show ----------


def _describe_profile_one_line(name: str, prof: TranscriptLLMProfileConfig) -> str:
    bits: list[str] = []
    if prof.model:
        bits.append(f"model={prof.model}")
    if prof.temperature is not None:
        bits.append(f"temperature={prof.temperature}")
    suffix = ("  " + " ".join(bits)) if bits else ""
    return f"  {name}{suffix}"


def _profile_body_lines(prof: TranscriptLLMProfileConfig) -> list[str]:
    lines: list[str] = []
    if prof.model:
        lines.append(f"  model: {prof.model}")
    if prof.temperature is not None:
        lines.append(f"  temperature: {prof.temperature}")
    for attr in ("system_prompt", "user_prompt", "user_prompt_template"):
        value = getattr(prof, attr)
        if not value:
            continue
        lines.append(f"  {attr}:")
        for sub in str(value).splitlines() or [str(value)]:
            lines.append(f"    {sub}")
    return lines


def _describe_profile_full(name: str, prof: TranscriptLLMProfileConfig) -> str:
    return "\n".join([f"{name} (LLM profile):", *_profile_body_lines(prof)])


def _describe_resolved_profile_block(name: str, prof: TranscriptLLMProfileConfig) -> str:
    return "\n".join([f"Resolved profile ({name}):", *_profile_body_lines(prof)])


@triggers_group.command("show")
@click.argument("name", required=False)
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to a triggers.json file (defaults to the canonical path).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit the resolved config as JSON instead of formatted text.",
)
def triggers_show(name: str | None, path_override: str | None, json_output: bool) -> None:
    """Show resolved triggers config.

    Without NAME: list trigger prefixes, dispatch settings, verbs, and LLM
    profiles. With NAME: show the full config for a verb (with resolved
    profile inlined) or LLM profile by that name. Verbs take precedence on
    name collision; a note is printed if both exist.
    """
    from voicepipe.transcript_triggers._help import (
        _describe_verb_full,
        _describe_verb_one_line,
    )

    triggers, dispatch, verbs, profiles = _load_config_or_exit(path_override)

    if name is None:
        if json_output:
            output: dict[str, Any] = {
                "triggers": dict(triggers),
                "dispatch": dataclasses.asdict(dispatch),
                "verbs": {n: dataclasses.asdict(verbs[n]) for n in sorted(verbs)},
                "profiles": {n: dataclasses.asdict(profiles[n]) for n in sorted(profiles)},
            }
            click.echo(json.dumps(output, indent=2, ensure_ascii=False))
            return

        click.echo(f"Triggers ({len(triggers)}):")
        if triggers:
            for prefix in sorted(triggers):
                click.echo(f"  {prefix} -> {triggers[prefix]}")
        else:
            click.echo("  (none)")
        click.echo("")
        click.echo("Dispatch settings:")
        click.echo(f"  unknown_verb: {dispatch.unknown_verb}")
        click.echo(f"  error_destination: {dispatch.error_destination}")
        click.echo("")
        click.echo(f"Verbs ({len(verbs)}):")
        if verbs:
            for verb_name in sorted(verbs):
                click.echo(_describe_verb_one_line(verb_name, verbs[verb_name]))
        else:
            click.echo("  (none)")
        click.echo("")
        click.echo(f"LLM profiles ({len(profiles)}):")
        if profiles:
            for prof_name in sorted(profiles):
                click.echo(_describe_profile_one_line(prof_name, profiles[prof_name]))
        else:
            click.echo("  (none)")
        return

    lookup = name.strip().lower()

    if lookup in verbs:
        verb_cfg = verbs[lookup]
        resolved_profile_name = (verb_cfg.profile or "").strip()
        resolved_profile = profiles.get(resolved_profile_name) if resolved_profile_name else None

        if json_output:
            payload: dict[str, Any] = {
                "verb": {"name": lookup, **dataclasses.asdict(verb_cfg)},
            }
            if resolved_profile_name:
                if resolved_profile is not None:
                    payload["resolved_profile"] = {
                        "name": resolved_profile_name,
                        **dataclasses.asdict(resolved_profile),
                    }
                else:
                    payload["resolved_profile"] = {
                        "name": resolved_profile_name,
                        "missing": True,
                    }
            if lookup in profiles:
                payload["profile_with_same_name"] = lookup
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        click.echo(_describe_verb_full(lookup, verb_cfg))
        if resolved_profile_name:
            click.echo("")
            if resolved_profile is not None:
                click.echo(_describe_resolved_profile_block(resolved_profile_name, resolved_profile))
            else:
                click.echo(
                    f"Profile {resolved_profile_name!r} is referenced but not defined."
                )
        if lookup in profiles:
            click.echo("")
            click.echo(
                f"Note: an LLM profile named {lookup!r} also exists; verb shown above."
            )
        return

    if lookup in profiles:
        prof = profiles[lookup]
        if json_output:
            click.echo(
                json.dumps(
                    {"profile": {"name": lookup, **dataclasses.asdict(prof)}},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        click.echo(_describe_profile_full(lookup, prof))
        return

    known_verbs = ", ".join(sorted(verbs)) or "(none)"
    known_profiles = ", ".join(sorted(profiles)) or "(none)"
    click.echo(f"✗ no verb or profile named {name!r}", err=True)
    click.echo(f"  known verbs: {known_verbs}", err=True)
    click.echo(f"  known profiles: {known_profiles}", err=True)
    sys.exit(1)
