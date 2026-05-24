"""`voicepipe triggers …` commands."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click

from voicepipe.config import (
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
    VoicepipeConfigError,
    triggers_json_path,
    validate_triggers_json,
)


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

    try:
        triggers, dispatch, verbs, profiles = validate_triggers_json(path=path)
    except FileNotFoundError:
        click.echo(f"✗ triggers.json not found: {path}", err=True)
        sys.exit(1)
    except VoicepipeConfigError as e:
        click.echo(f"✗ triggers.json invalid: {path}", err=True)
        click.echo(f"  {e}", err=True)
        sys.exit(1)

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
