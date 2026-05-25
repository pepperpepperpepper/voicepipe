"""Confirm-then-execute flow: stash + yes/no resume.

When a verb has ``confirm: true``, the shell/execute/codegen handlers
call :func:`_stash_pending_and_notice` instead of executing — that
saves the command (or generated script) to the single-slot pending
store and returns a "Pending … — say 'zwingli yes' to confirm" notice.
The user's next utterance routes through :func:`_action_yes` or
:func:`_action_no`.

:func:`_action_yes` resumes the stashed entry by calling the matching
runner. Because the runners live in sibling modules (``_shell``,
``_codegen``) and the tests rely on monkeypatching those runners,
``_action_yes`` looks them up via *module attribute access* at call
time (``_shell._run_shell_command(...)``) rather than binding the
name at import. This lets ``monkeypatch.setattr(tt._shell,
"_run_shell_command", fake)`` intercept resumed-pending execution too.
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._actuator import Actuator


def _stash_pending_and_notice(
    *,
    verb_cfg: TranscriptVerbConfig,
    verb_type: str,
    command: str,
    interpreter: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Save a pending command for the confirm-then-execute flow and return a
    user-facing notice + meta. Shared by shell, execute, and codegen confirm
    paths.

    Pass `interpreter` for codegen entries (verb_type="script"); the notice
    then renders the generated script body with a header naming the runner.
    """
    from voicepipe import pending

    raw_timeout = getattr(verb_cfg, "confirm_timeout_seconds", None)
    timeout = (
        float(raw_timeout)
        if isinstance(raw_timeout, (int, float)) and raw_timeout > 0
        else pending.DEFAULT_TIMEOUT_SECONDS
    )
    verb_name = (getattr(verb_cfg, "action", "") or verb_type).strip().lower()
    entry = pending.make_pending(
        verb=verb_name,
        verb_type=verb_type,
        command=command,
        timeout_seconds=timeout,
        interpreter=interpreter,
    )
    pending.save_pending(entry)
    if verb_type == "script" and interpreter:
        notice = (
            f"Pending {interpreter} script:\n{command}\n"
            "— say 'zwingli yes' to confirm or 'zwingli no' to cancel."
        )
    else:
        notice = (
            f"Pending {verb_type}: {command} — "
            "say 'zwingli yes' to confirm or 'zwingli no' to cancel."
        )
    meta: dict[str, Any] = {
        "pending": True,
        "pending_verb_type": verb_type,
        "pending_command": command,
        "pending_timeout_seconds": timeout,
    }
    if interpreter:
        meta["pending_interpreter"] = interpreter
    return notice, meta


def _action_yes(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Resume a previously-stashed pending command. Args are ignored."""
    del prompt, verb_cfg, profiles, captures, commands
    from voicepipe import pending as pending_mod

    entry = pending_mod.load_pending()
    if entry is None:
        return (
            "No pending command to confirm (none stashed or it expired).",
            {"no_pending": True},
        )

    pending_mod.clear_pending()
    if entry.verb_type == "shell":
        # Module-attribute lookup at call time so monkeypatching
        # `_shell._run_shell_command` intercepts the resumed call.
        from voicepipe.transcript_triggers import _shell

        stdout, stderr, run_meta = _shell._run_shell_command(
            entry.command, timeout_seconds=None, actuator=actuator
        )
        output = stdout if stdout.strip() else stderr
        output = (output or "").rstrip("\n")
        run_meta["resumed_pending"] = True
        run_meta["pending_verb"] = entry.verb
        return output, run_meta
    if entry.verb_type == "execute":
        return entry.command, {
            "enter": True,
            "resumed_pending": True,
            "pending_verb": entry.verb,
        }
    if entry.verb_type == "script":
        interpreter = (entry.interpreter or "").strip()
        if not interpreter:
            return (
                "Pending script has no interpreter; cleared without action.",
                {
                    "resumed_pending": False,
                    "pending_verb": entry.verb,
                    "error": "missing_interpreter",
                },
            )
        from voicepipe.transcript_triggers import _codegen

        stdout, stderr, run_meta = _codegen._run_script_in_interpreter(
            interpreter, entry.command, timeout_seconds=None, actuator=actuator
        )
        output = stdout if stdout.strip() else stderr
        output = (output or "").rstrip("\n")
        run_meta["resumed_pending"] = True
        run_meta["pending_verb"] = entry.verb
        return output, run_meta
    return (
        f"Pending command has unknown verb_type {entry.verb_type!r}; cleared without action.",
        {"resumed_pending": False, "pending_verb_type": entry.verb_type},
    )


def _action_no(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Cancel a previously-stashed pending command. Args are ignored."""
    del prompt, verb_cfg, profiles, captures, commands, actuator
    from voicepipe import pending as pending_mod

    entry = pending_mod.load_pending()
    pending_mod.clear_pending()
    if entry is None:
        return ("No pending command to cancel.", {"no_pending": True})
    return (
        f"Cancelled pending {entry.verb_type}: {entry.command}",
        {"cancelled": True, "pending_verb": entry.verb, "pending_verb_type": entry.verb_type},
    )
