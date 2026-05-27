"""`voicepipe triggers …` commands."""

from __future__ import annotations

import dataclasses
import json
import os
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
from voicepipe.transcript_triggers._phrase import (
    normalize_phrase as _normalize_phrase,
    validate_phrase as _validate_phrase,
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
    references, codegen verbs missing required fields or whose interpreter
    isn't installed, alias collisions across verbs, and disabled verbs
    that still carry dead alias config.
    """
    warnings: list[str] = []

    # Profile references that don't resolve.
    for verb_name, cfg in sorted(verbs.items()):
        profile = (cfg.profile or "").strip()
        if profile and profile not in profiles:
            warnings.append(
                f"verb {verb_name!r}: profile {profile!r} is not defined in llm_profiles"
            )

    # Enabled codegen verbs without a profile. (The parser already
    # enforces a non-empty `interpreter` for any codegen verb, so there's
    # no need to re-check that here.) Profile is what tells the LLM how
    # to generate the script; without it, codegen falls back to a generic
    # call that's almost certainly not what the user wants.
    for verb_name, cfg in sorted(verbs.items()):
        if (cfg.type or "").strip().lower() != "codegen":
            continue
        if not bool(cfg.enabled):
            continue
        if not (cfg.profile or "").strip():
            warnings.append(
                f"verb {verb_name!r}: codegen verb has no `profile` set "
                "(codegen needs an LLM to generate the script)"
            )

    # Codegen interpreters not on PATH (only meaningful when interpreter
    # IS set; the missing-interpreter check above handles the unset case).
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

    # Disabled verbs that still declare aliases — the aliases can never
    # resolve, so they're dead config (and a likely sign the user
    # forgot to remove them after disabling a verb).
    for verb_name, cfg in sorted(verbs.items()):
        if bool(cfg.enabled):
            continue
        if not cfg.aliases:
            continue
        joined = ", ".join(repr(a) for a in cfg.aliases)
        warnings.append(
            f"verb {verb_name!r} is disabled but declares aliases ({joined}) "
            "that will never resolve"
        )

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
    suggestions = step.get("did_you_mean")
    if isinstance(suggestions, list) and suggestions:
        lines.append(f"    did_you_mean: {', '.join(suggestions)}")

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


# ---------- triggers log ----------


def _format_ts(ts_ms: Any) -> str:
    """Render a ts_ms field as local time. Returns a placeholder if unparseable."""
    import datetime

    try:
        seconds = float(ts_ms) / 1000.0
    except (TypeError, ValueError):
        return "????-??-?? ??:??:??"
    try:
        return datetime.datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "????-??-?? ??:??:??"


def _snippet(value: Any, *, limit: int = 80) -> str:
    """Single-line, length-capped repr for log summaries."""
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _summarize_event(ev: dict[str, Any]) -> str:
    """Per-event-type one-line summary string."""
    name = ev.get("event", "")

    if name == "trigger_match":
        return (
            f"trigger={ev.get('trigger')!r} action={ev.get('action')!r} "
            f"text={_snippet(ev.get('text'))!r}"
        )
    if name in ("dispatch_ok", "action_ok"):
        bits = []
        if "trigger" in ev:
            bits.append(f"trigger={ev.get('trigger')!r}")
        if "action" in ev:
            bits.append(f"action={ev.get('action')!r}")
        meta = ev.get("meta")
        if isinstance(meta, dict) and meta.get("mode") == "unknown-verb":
            attempted = meta.get("verb")
            if attempted:
                bits.append(f"unknown_verb={attempted!r}")
            suggestions = meta.get("did_you_mean")
            if isinstance(suggestions, list) and suggestions:
                bits.append(f"did_you_mean={','.join(suggestions)}")
        bits.append(f"output={_snippet(ev.get('output_text'))!r}")
        return " ".join(bits)
    if name in ("dispatch_error", "action_error"):
        bits = []
        if "trigger" in ev:
            bits.append(f"trigger={ev.get('trigger')!r}")
        if "action" in ev:
            bits.append(f"action={ev.get('action')!r}")
        bits.append(f"error={_snippet(ev.get('error'))!r}")
        return " ".join(bits)
    if name == "action_missing":
        return f"action={ev.get('action')!r} (no handler)"
    if name == "rate_limited":
        bits = [f"verb={ev.get('verb')!r}"]
        if "retry_after_seconds" in ev:
            bits.append(f"retry_after={ev['retry_after_seconds']}s")
        cap = ev.get("cap_per_min") if "cap_per_min" in ev else ev.get("limit")
        if cap is not None:
            bits.append(f"cap_per_min={cap}")
        return " ".join(bits)
    if name in ("shell_start", "codegen_start"):
        return f"command={_snippet(ev.get('command'))!r}"
    if name in ("shell_complete", "codegen_complete"):
        bits = []
        if "returncode" in ev:
            bits.append(f"rc={ev['returncode']}")
        if ev.get("stdout"):
            bits.append(f"stdout={_snippet(ev.get('stdout'))!r}")
        if ev.get("stderr"):
            bits.append(f"stderr={_snippet(ev.get('stderr'))!r}")
        return " ".join(bits) or "(no output)"
    if name in ("shell_blocked", "codegen_blocked"):
        return f"reason={_snippet(ev.get('reason') or ev.get('error'))!r}"
    if name in ("shell_timeout", "codegen_timeout"):
        bits = []
        if "timeout_seconds" in ev:
            bits.append(f"timeout={ev['timeout_seconds']}s")
        if ev.get("command"):
            bits.append(f"command={_snippet(ev.get('command'))!r}")
        return " ".join(bits)

    # Unknown event type: dump non-noise keys.
    rest = {
        k: v for k, v in ev.items() if k not in ("event", "ts_ms", "pid")
    }
    return json.dumps(rest, ensure_ascii=False, default=str)


def _read_debug_log_tail(path: Path, tail: int) -> list[dict[str, Any]]:
    """Return the last `tail` parsed JSON-line events from `path`. Bad lines are skipped."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text:
        return []
    raw_lines = [line for line in text.splitlines() if line.strip()]
    if tail > 0:
        raw_lines = raw_lines[-tail:]
    events: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _format_log_line(ev: dict[str, Any]) -> str:
    """One-line human-readable rendering of a debug event."""
    ts = _format_ts(ev.get("ts_ms"))
    name = ev.get("event", "?")
    return f"{ts}  {name:<20}  {_summarize_event(ev)}"


def _iter_follow_log(path: Path, *, poll_seconds: float = 0.5):
    """Yield newly-appended lines from `path` until interrupted.

    Opens the file, seeks to end, then polls for new content. Detects log
    rotation (inode change) and reopens from the new file's beginning so
    nothing is lost across a rotation. Partial trailing lines are
    buffered until a newline arrives. Caller handles KeyboardInterrupt.
    """
    import time

    f = open(path, "r", encoding="utf-8", errors="replace")
    try:
        f.seek(0, 2)  # SEEK_END
        try:
            inode: int | None = os.fstat(f.fileno()).st_ino
        except OSError:
            inode = None
        buffer = ""
        while True:
            chunk = f.read()
            if chunk:
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    yield line
                continue
            try:
                new_inode: int | None = path.stat().st_ino
            except FileNotFoundError:
                # File temporarily missing during rotation; keep polling.
                time.sleep(poll_seconds)
                continue
            except OSError:
                new_inode = None
            if (
                inode is not None
                and new_inode is not None
                and new_inode != inode
            ):
                f.close()
                f = open(path, "r", encoding="utf-8", errors="replace")
                inode = new_inode
                buffer = ""
                continue
            time.sleep(poll_seconds)
    finally:
        f.close()


@triggers_group.command("log")
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to the Zwingli debug log (defaults to VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE or /tmp/zwingli-debug.log).",
)
@click.option(
    "--tail",
    "tail",
    type=int,
    default=20,
    show_default=True,
    help="Show only the last N events. Use 0 for all.",
)
@click.option(
    "--follow",
    "-f",
    "follow",
    is_flag=True,
    help="After the initial tail, keep printing new events as they arrive (Ctrl-C to stop).",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit raw JSON-line events instead of formatted text.",
)
def triggers_log(
    path_override: str | None,
    tail: int,
    follow: bool,
    json_output: bool,
) -> None:
    """Print recent Zwingli dispatch debug events.

    The debug log is a JSON-lines file written by the dispatch pipeline
    on every trigger match, action invocation, shell/codegen execution,
    and rate-limit block. By default this command tails the last N events
    and exits. Pass --follow to also stream new events as they arrive
    (like `tail -f`). Pass --json for the raw line stream.
    """
    from voicepipe.transcript_triggers._debug_log import _zwingli_debug_log_path

    path = Path(path_override).expanduser() if path_override else _zwingli_debug_log_path()
    if not path.exists():
        click.echo(f"✗ debug log not found: {path}", err=True)
        click.echo(
            "  (set VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE or run a trigger to create it)",
            err=True,
        )
        sys.exit(1)

    try:
        events = _read_debug_log_tail(path, tail)
    except OSError as e:
        click.echo(f"✗ could not read debug log: {path}", err=True)
        click.echo(f"  {e}", err=True)
        sys.exit(1)

    if json_output:
        for ev in events:
            click.echo(json.dumps(ev, ensure_ascii=False))
    elif not events:
        if not follow:
            click.echo(f"(no events in {path})")
            return
        click.echo(f"(no events yet in {path}; waiting…)")
    else:
        for ev in events:
            click.echo(_format_log_line(ev))

    if not follow:
        return

    try:
        for line in _iter_follow_log(path):
            if not line.strip():
                continue
            if json_output:
                click.echo(line)
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, dict):
                click.echo(_format_log_line(ev))
    except KeyboardInterrupt:
        return


# ---------- triggers path ----------


@triggers_group.command("path")
def triggers_path() -> None:
    """Print the canonical triggers.json path (whether or not the file exists).

    Handy for shell composition, e.g. ``$EDITOR "$(voicepipe triggers path)"``.
    """
    click.echo(str(triggers_json_path()))


# ---------- triggers stats ----------


def _extract_verb_from_event(ev: dict[str, Any]) -> str | None:
    """Return the verb name associated with `ev`, or None if not applicable.

    - dispatch_ok / dispatch_error: ``meta.verb`` (set by the dispatcher
      after verb resolution; absent if resolution itself failed)
    - action_ok / action_error / action_missing: the ``action`` field is
      already the verb name (e.g. "shell", "python")
    - rate_limited: the ``verb`` field
    - shell_* / codegen_*: no direct verb context — we don't try to
      correlate against preceding dispatch events
    """
    name = ev.get("event")
    if name in ("dispatch_ok", "dispatch_error"):
        meta = ev.get("meta")
        if isinstance(meta, dict):
            verb = meta.get("verb")
            if isinstance(verb, str) and verb:
                return verb
        return None
    if name in ("action_ok", "action_error", "action_missing"):
        action = ev.get("action")
        if isinstance(action, str) and action:
            return action
        return None
    if name == "rate_limited":
        verb = ev.get("verb")
        if isinstance(verb, str) and verb:
            return verb
        return None
    return None


def _aggregate_log_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll a list of debug events into the summary dict that drives both
    the text and JSON outputs of `triggers stats`."""
    from collections import Counter

    event_counts: Counter[str] = Counter()
    trigger_counts: Counter[str] = Counter()
    verb_ok: Counter[str] = Counter()
    verb_error: Counter[str] = Counter()
    verb_rate_limited: Counter[str] = Counter()
    verb_unknown: Counter[str] = Counter()
    first_ts: int | None = None
    last_ts: int | None = None

    for ev in events:
        name = ev.get("event")
        if not isinstance(name, str):
            continue
        event_counts[name] += 1

        ts = ev.get("ts_ms")
        if isinstance(ts, (int, float)):
            ts_int = int(ts)
            if first_ts is None or ts_int < first_ts:
                first_ts = ts_int
            if last_ts is None or ts_int > last_ts:
                last_ts = ts_int

        if name == "trigger_match":
            trig = ev.get("trigger")
            if isinstance(trig, str) and trig:
                trigger_counts[trig] += 1

        verb = _extract_verb_from_event(ev)
        if verb is not None:
            if name in ("dispatch_ok", "action_ok"):
                verb_ok[verb] += 1
            elif name in ("dispatch_error", "action_error"):
                verb_error[verb] += 1
            elif name == "rate_limited":
                verb_rate_limited[verb] += 1
            elif name == "action_missing":
                verb_unknown[verb] += 1

    all_verbs = (
        set(verb_ok)
        | set(verb_error)
        | set(verb_rate_limited)
        | set(verb_unknown)
    )
    verb_summary: dict[str, dict[str, int]] = {}
    for verb in all_verbs:
        verb_summary[verb] = {
            "ok": int(verb_ok[verb]),
            "error": int(verb_error[verb]),
            "rate_limited": int(verb_rate_limited[verb]),
            "unknown": int(verb_unknown[verb]),
            "total": int(
                verb_ok[verb] + verb_error[verb] + verb_rate_limited[verb] + verb_unknown[verb]
            ),
        }

    return {
        "total_events": int(sum(event_counts.values())),
        "first_event_ms": first_ts,
        "last_event_ms": last_ts,
        "event_counts": dict(event_counts.most_common()),
        "trigger_counts": dict(trigger_counts.most_common()),
        "verb_counts": verb_summary,
    }


def _format_stats_text(stats: dict[str, Any], path: Path, *, top: int) -> list[str]:
    """Render the aggregate dict as human-readable lines."""
    lines: list[str] = []
    total = stats.get("total_events", 0)
    first_ms = stats.get("first_event_ms")
    last_ms = stats.get("last_event_ms")
    if first_ms is not None and last_ms is not None:
        lines.append(
            f"Stats from {path} ({total} events, "
            f"{_format_ts(first_ms)} to {_format_ts(last_ms)})"
        )
    else:
        lines.append(f"Stats from {path} ({total} events)")
    lines.append("")

    triggers = stats.get("trigger_counts") or {}
    lines.append("Triggers:")
    if triggers:
        for trig, count in list(triggers.items())[:top] if top > 0 else triggers.items():
            lines.append(f"  {trig:<20} {count}")
    else:
        lines.append("  (none)")
    lines.append("")

    verbs = stats.get("verb_counts") or {}
    ordered = sorted(verbs.items(), key=lambda kv: -kv[1]["total"])
    if top > 0:
        ordered = ordered[:top]
    lines.append("Verbs by call count:")
    if ordered:
        for verb, counts in ordered:
            bits = [f"{counts['ok']} ok"]
            if counts["error"]:
                bits.append(f"{counts['error']} err")
            if counts["rate_limited"]:
                bits.append(f"{counts['rate_limited']} limited")
            if counts["unknown"]:
                bits.append(f"{counts['unknown']} unknown")
            lines.append(
                f"  {verb:<20} {counts['total']:>6}  (" + ", ".join(bits) + ")"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    event_counts = stats.get("event_counts") or {}
    lines.append("Event types:")
    if event_counts:
        for name, count in event_counts.items():
            lines.append(f"  {name:<20} {count}")
    else:
        lines.append("  (none)")
    return lines


@triggers_group.command("stats")
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to the Zwingli debug log (defaults to VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE or /tmp/zwingli-debug.log).",
)
@click.option(
    "--top",
    "top",
    type=int,
    default=10,
    show_default=True,
    help="Show only the top N triggers / verbs. Use 0 for all.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Emit aggregate counts as JSON instead of formatted text.",
)
def triggers_stats(path_override: str | None, top: int, json_output: bool) -> None:
    """Aggregate the Zwingli debug log into usage stats.

    Reads the JSON-lines debug log and rolls it up by event type, trigger
    prefix, and verb. Useful for "which verbs do I actually use?" and
    "where are my dispatch errors coming from?" without grepping the raw
    log. Counts are derived from the events visible in the log file — no
    inference across rotation boundaries.
    """
    from voicepipe.transcript_triggers._debug_log import _zwingli_debug_log_path

    path = Path(path_override).expanduser() if path_override else _zwingli_debug_log_path()
    if not path.exists():
        click.echo(f"✗ debug log not found: {path}", err=True)
        click.echo(
            "  (set VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE or run a trigger to create it)",
            err=True,
        )
        sys.exit(1)

    try:
        # tail=0 reads everything; stats are most useful aggregated across
        # the entire visible log.
        events = _read_debug_log_tail(path, 0)
    except OSError as e:
        click.echo(f"✗ could not read debug log: {path}", err=True)
        click.echo(f"  {e}", err=True)
        sys.exit(1)

    stats = _aggregate_log_stats(events)

    if json_output:
        click.echo(json.dumps(stats, indent=2, ensure_ascii=False))
        return

    if stats["total_events"] == 0:
        click.echo(f"(no events in {path})")
        return

    for line in _format_stats_text(stats, path, top=top):
        click.echo(line)


# ---------- triggers add / triggers remove ----------
#
# Validation/normalization rules live in voicepipe.transcript_triggers._phrase
# so the PATCH /triggers HTTP endpoint can apply the exact same rules.


def _read_triggers_payload(path: Path) -> dict[str, Any]:
    """Read triggers.json into a plain dict (no validation)."""
    if not path.exists():
        click.echo(f"✗ triggers.json not found: {path}", err=True)
        click.echo("  Run `voicepipe setup` to create one.", err=True)
        sys.exit(1)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        click.echo(f"✗ could not read {path}: {e}", err=True)
        sys.exit(1)
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        click.echo(f"✗ triggers.json is not valid JSON: {path}", err=True)
        click.echo(f"  {e}", err=True)
        sys.exit(1)
    if not isinstance(payload, dict):
        click.echo(
            f"✗ triggers.json must contain a JSON object: {path}", err=True,
        )
        sys.exit(1)
    return payload


def _write_triggers_payload(path: Path, payload: dict[str, Any]) -> None:
    """Write triggers.json with the same formatting `voicepipe setup` uses."""
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        path.write_text(rendered, encoding="utf-8")
    except OSError as e:
        click.echo(f"✗ could not write {path}: {e}", err=True)
        sys.exit(1)


def _existing_trigger_keys(payload: dict[str, Any]) -> dict[str, str]:
    """Return a {normalized_phrase: original_key} map for the current triggers.

    Used to detect collisions case- and whitespace-insensitively while still
    being able to address the original key for an in-place removal.
    """
    section = payload.get("triggers")
    if not isinstance(section, dict):
        return {}
    out: dict[str, str] = {}
    for key in section.keys():
        if isinstance(key, str):
            out[_normalize_phrase(key)] = key
    return out


@triggers_group.command("add")
@click.argument("phrase", required=True)
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to a triggers.json file (defaults to the canonical path).",
)
def triggers_add(phrase: str, path_override: str | None) -> None:
    """Add a new activation phrase to triggers.json.

    The phrase is normalized (lowercased, whitespace collapsed) and added
    with ``action: "dispatch"`` so it routes through the verb dispatcher
    just like the built-in ``zwingli`` trigger. Existing triggers are
    preserved. Adding a phrase that already exists is a no-op (exit 0).
    """
    normalized = _normalize_phrase(phrase)
    reason = _validate_phrase(normalized)
    if reason is not None:
        click.echo(f"✗ invalid phrase: {reason}", err=True)
        sys.exit(1)

    path = Path(path_override).expanduser() if path_override else triggers_json_path()
    payload = _read_triggers_payload(path)

    section = payload.get("triggers")
    if not isinstance(section, dict):
        section = {}
        payload["triggers"] = section

    existing = _existing_trigger_keys(payload)
    if normalized in existing:
        click.echo(
            f"= trigger {normalized!r} already present (no change): {path}"
        )
        return

    section[normalized] = {"action": "dispatch"}
    _write_triggers_payload(path, payload)
    click.echo(f"✓ added trigger {normalized!r} to {path}")


@triggers_group.command("remove")
@click.argument("phrase", required=True)
@click.option(
    "--path",
    "path_override",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to a triggers.json file (defaults to the canonical path).",
)
def triggers_remove(phrase: str, path_override: str | None) -> None:
    """Remove an activation phrase from triggers.json.

    Refuses to remove the last remaining trigger, since that would leave
    the dispatcher unable to recognize any voice command. Removing a
    phrase that isn't configured is a no-op (exit 0).
    """
    normalized = _normalize_phrase(phrase)
    if not normalized:
        click.echo("✗ invalid phrase: phrase is empty", err=True)
        sys.exit(1)

    path = Path(path_override).expanduser() if path_override else triggers_json_path()
    payload = _read_triggers_payload(path)

    section = payload.get("triggers")
    if not isinstance(section, dict) or not section:
        click.echo(f"= trigger {normalized!r} not present (no change): {path}")
        return

    existing = _existing_trigger_keys(payload)
    original_key = existing.get(normalized)
    if original_key is None:
        click.echo(f"= trigger {normalized!r} not present (no change): {path}")
        return

    if len(section) <= 1:
        click.echo(
            f"✗ refusing to remove the last trigger ({original_key!r}); "
            "the dispatcher would no longer match any voice commands.",
            err=True,
        )
        click.echo(
            "  Add another phrase first with `voicepipe triggers add <phrase>`.",
            err=True,
        )
        sys.exit(1)

    del section[original_key]
    _write_triggers_payload(path, payload)
    click.echo(f"✓ removed trigger {original_key!r} from {path}")
