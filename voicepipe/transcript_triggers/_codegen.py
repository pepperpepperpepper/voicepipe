"""``codegen`` action: LLM generates a script, voicepipe runs it.

Distinct from ``shell``: codegen sends the user's spoken request to an
LLM (via the verb's profile), strips any markdown fence the model might
have produced, writes the script body to a tempfile, and runs
``<interpreter> <tempfile>``. The execution path uses list-form argv
(not ``shell=True``), so the interpreter name isn't shell-expanded.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._debug_log import _write_zwingli_debug_event
from ._llm import _call_llm_with_profile
from ._pending import _stash_pending_and_notice
from ._shell import _resolve_shell_timeout_seconds


def _strip_code_fences(text: str) -> str:
    """Remove a single leading ```lang line and trailing ``` line if present.

    LLMs frequently wrap script output in markdown fences despite a system
    prompt asking them not to. This is conservative — it only strips a
    well-formed fence on the first and last lines.
    """
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    if len(lines) < 2:
        return cleaned
    body = lines[1:]
    if body and body[-1].strip() == "```":
        body = body[:-1]
    return "\n".join(body).strip()


def _run_script_in_interpreter(
    interpreter: str,
    script: str,
    *,
    timeout_seconds: float | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Write `script` to a tempfile and invoke `interpreter <tempfile>`.

    Mirrors :func:`_run_shell_command` but uses list-form argv (no shell
    expansion of the interpreter or path). Same VOICEPIPE_SHELL_ALLOW gate.
    """
    cleaned = (script or "").strip()
    if not cleaned:
        return "", "", {"returncode": 0, "duration_ms": 0}

    if (os.environ.get("VOICEPIPE_SHELL_ALLOW") or "").strip() != "1":
        _write_zwingli_debug_event(
            {
                "event": "codegen_blocked",
                "interpreter": interpreter,
                "shell_allow": (os.environ.get("VOICEPIPE_SHELL_ALLOW") or "").strip(),
            }
        )
        raise RuntimeError(
            "Codegen execution is disabled. Set VOICEPIPE_SHELL_ALLOW=1 to enable."
        )

    timeout_s = _resolve_shell_timeout_seconds(timeout_seconds=timeout_seconds)

    fd, script_path = tempfile.mkstemp(prefix="voicepipe-codegen-", suffix=".script")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(cleaned)

        started = time.monotonic()
        _write_zwingli_debug_event(
            {
                "event": "codegen_start",
                "interpreter": interpreter,
                "script_path": script_path,
                "command": cleaned,
                "timeout_seconds": float(timeout_s),
            }
        )
        try:
            proc = subprocess.run(
                [interpreter, script_path],
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
                "interpreter": interpreter,
            }
            if proc.returncode != 0:
                meta["error"] = "nonzero-exit"
            _write_zwingli_debug_event(
                {
                    "event": "codegen_complete",
                    "interpreter": interpreter,
                    "returncode": int(proc.returncode),
                    "duration_ms": int(duration_ms),
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
            return stdout, stderr, meta
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.monotonic() - started) * 1000)
            raw_stdout = getattr(e, "stdout", None) or getattr(e, "output", None)
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
                "interpreter": interpreter,
                "error": "timeout",
            }
            _write_zwingli_debug_event(
                {
                    "event": "codegen_timeout",
                    "interpreter": interpreter,
                    "duration_ms": int(duration_ms),
                    "timeout_seconds": float(timeout_s),
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
            return str(stdout), str(stderr), meta
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass


def _action_codegen(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    """Generate a script with the LLM, then run it through `interpreter`.

    The LLM call uses the verb's configured `profile` exactly like
    :func:`_action_zwingli`. The generated script is written to a tempfile
    and executed via ``<interpreter> <tempfile>``. Honors ``confirm``:
    when true, the *generated script* (not the user's phrase) is stashed
    for 'zwingli yes' to run.
    """
    del commands
    if verb_cfg is None:
        raise RuntimeError("Codegen verb is missing configuration (verb_cfg=None)")

    interpreter = (getattr(verb_cfg, "interpreter", "") or "").strip()
    if not interpreter:
        raise RuntimeError(
            f"Codegen verb {getattr(verb_cfg, 'action', '?')!r} is missing 'interpreter'"
        )

    profile_name = (getattr(verb_cfg, "profile", "") or "").strip().lower()
    profile: TranscriptLLMProfileConfig | None = None
    if profile_name and profiles is not None:
        profile = profiles.get(profile_name)

    script_text, llm_meta = _call_llm_with_profile(
        prompt, profile=profile, captures=captures
    )
    if profile_name:
        llm_meta["profile_found"] = profile is not None
    script_text = _strip_code_fences(script_text)

    if not script_text.strip():
        return "", {"empty_script": True, "interpreter": interpreter, **llm_meta}

    if getattr(verb_cfg, "confirm", False):
        notice, stash_meta = _stash_pending_and_notice(
            verb_cfg=verb_cfg,
            verb_type="script",
            command=script_text,
            interpreter=interpreter,
        )
        merged = {**llm_meta, **stash_meta, "generated_script": script_text}
        return notice, merged

    timeout_seconds = getattr(verb_cfg, "timeout_seconds", None)
    stdout, stderr, run_meta = _run_script_in_interpreter(
        interpreter, script_text, timeout_seconds=timeout_seconds
    )
    output = stdout if stdout.strip() else stderr
    output = (output or "").rstrip("\n")
    merged = {**llm_meta, **run_meta, "generated_script": script_text}
    return output, merged
