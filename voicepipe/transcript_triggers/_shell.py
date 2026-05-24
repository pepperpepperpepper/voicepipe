"""``shell`` and ``execute`` action handlers + the shared subprocess runner.

- ``_action_shell`` spawns a real subprocess (``bash -c <command>``) and
  returns stdout/stderr. Gated on ``VOICEPIPE_SHELL_ALLOW=1``.
- ``_action_execute`` does NOT spawn a subprocess; it returns the cleaned
  command text with ``{"enter": True}`` so the emission layer types it
  into the focused terminal and presses Enter.
- Both honor ``verb_cfg.confirm`` by stashing the command via
  :func:`_stash_pending_and_notice` (from ``_pending``) instead of
  executing.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._debug_log import _write_zwingli_debug_event
from ._pending import _stash_pending_and_notice
from ._template import _substitute_command_template


def _parse_positive_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except Exception:
            return None
    return None


def _resolve_shell_timeout_seconds(*, timeout_seconds: float | None = None) -> float:
    resolved = _parse_positive_float(timeout_seconds)
    if resolved is None:
        resolved = _parse_positive_float(os.environ.get("VOICEPIPE_SHELL_TIMEOUT_SECONDS"))
    if resolved is None:
        resolved = 10.0
    if resolved <= 0:
        resolved = 10.0
    return float(resolved)


def _strip_trailing_sentence_punct_from_shell_command(command: str) -> str:
    """Strip common STT sentence-ending punctuation from the final token.

    Example: "ls -la." -> "ls -la"
    This is intentionally conservative to avoid changing paths like "..".
    """
    cleaned = (command or "").strip()
    if not cleaned:
        return ""
    parts = cleaned.split()
    if not parts:
        return cleaned
    last = parts[-1]
    if last and all(ch == "." for ch in last):
        return cleaned
    trimmed_last = last.rstrip(".?!")
    if not trimmed_last or trimmed_last == last:
        return cleaned
    parts[-1] = trimmed_last
    return " ".join(parts)


def _run_shell_command(
    command: str, *, timeout_seconds: float | None = None
) -> tuple[str, str, dict[str, Any]]:
    cleaned = _strip_trailing_sentence_punct_from_shell_command(command)
    if not cleaned:
        return "", "", {"returncode": 0, "duration_ms": 0}

    if (os.environ.get("VOICEPIPE_SHELL_ALLOW") or "").strip() != "1":
        _write_zwingli_debug_event(
            {
                "event": "shell_blocked",
                "command": cleaned,
                "shell_allow": (os.environ.get("VOICEPIPE_SHELL_ALLOW") or "").strip(),
            }
        )
        raise RuntimeError(
            "Shell trigger action is disabled. Set VOICEPIPE_SHELL_ALLOW=1 to enable."
        )

    timeout_s = _resolve_shell_timeout_seconds(timeout_seconds=timeout_seconds)

    started = time.monotonic()
    _write_zwingli_debug_event(
        {
            "event": "shell_start",
            "command": cleaned,
            "timeout_seconds": float(timeout_s),
        }
    )
    try:
        proc = subprocess.run(
            cleaned,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            stdin=subprocess.DEVNULL,
        )
        duration_ms = int((time.monotonic() - started) * 1000)

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        meta: dict[str, Any] = {
            "returncode": int(proc.returncode),
            "duration_ms": duration_ms,
            "timeout_seconds": float(timeout_s),
        }
        if proc.returncode != 0:
            meta["error"] = "nonzero-exit"

        _write_zwingli_debug_event(
            {
                "event": "shell_complete",
                "command": cleaned,
                "returncode": int(proc.returncode),
                "duration_ms": int(duration_ms),
                "timeout_seconds": float(timeout_s),
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return stdout, stderr, meta
    except subprocess.TimeoutExpired as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        raw_stdout = getattr(e, "stdout", None)
        if raw_stdout is None:
            raw_stdout = getattr(e, "output", None)
        raw_stderr = getattr(e, "stderr", None)

        stdout = raw_stdout or ""
        stderr = raw_stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        meta = {
            "returncode": None,
            "duration_ms": duration_ms,
            "timeout_seconds": float(timeout_s),
            "error": "timeout",
        }
        _write_zwingli_debug_event(
            {
                "event": "shell_timeout",
                "command": cleaned,
                "duration_ms": int(duration_ms),
                "timeout_seconds": float(timeout_s),
                "stdout": stdout,
                "stderr": stderr,
            }
        )
        return str(stdout), str(stderr), meta


def _action_shell(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del profiles, commands
    timeout_seconds = getattr(verb_cfg, "timeout_seconds", None) if verb_cfg else None
    command_template = getattr(verb_cfg, "command_template", None) if verb_cfg else None
    if command_template and captures is not None:
        command = _substitute_command_template(command_template, captures)
    else:
        command = prompt

    if verb_cfg is not None and getattr(verb_cfg, "confirm", False):
        cleaned = (command or "").strip()
        if not cleaned:
            return "", {"pending": False, "reason": "empty_command"}
        return _stash_pending_and_notice(verb_cfg=verb_cfg, verb_type="shell", command=cleaned)

    stdout, stderr, meta = _run_shell_command(command, timeout_seconds=timeout_seconds)
    output = stdout if stdout.strip() else stderr
    output = (output or "").rstrip("\n")
    return output, meta


def _action_execute(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    """Prepare a shell command for *typing* into a terminal and pressing Enter.

    This action must never spawn a subprocess to run the command; it only
    returns the cleaned command text and metadata indicating that an Enter
    keystroke should be sent by the caller when typing is the destination.
    """
    del profiles, commands
    command_template = getattr(verb_cfg, "command_template", None) if verb_cfg else None
    if command_template and captures is not None:
        source = _substitute_command_template(command_template, captures)
    else:
        source = prompt
    cleaned = _strip_trailing_sentence_punct_from_shell_command(source)
    cleaned = (cleaned or "").strip()
    if not cleaned:
        return "", {"enter": False}
    if verb_cfg is not None and getattr(verb_cfg, "confirm", False):
        return _stash_pending_and_notice(
            verb_cfg=verb_cfg, verb_type="execute", command=cleaned
        )
    return cleaned, {"enter": True}
