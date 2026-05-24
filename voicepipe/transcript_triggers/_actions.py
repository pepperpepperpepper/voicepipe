"""All Zwingli action handlers (``_action_*``) and their direct helpers.

Each handler signature is ``(prompt, *, verb_cfg, profiles, captures, commands) -> (text, meta)``.
The handlers live together because they share a small set of helpers
(``_run_shell_command``, ``_run_script_in_interpreter``, ``_call_llm_with_profile``,
``_stash_pending_and_notice``) and grouping them in one file keeps the
call sites colocated with what they call. The ``_ACTIONS`` dispatch
table sits at the bottom.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptPluginConfig,
    TranscriptVerbConfig,
)

from ._debug_log import _write_zwingli_debug_event
from ._template import (
    _render_user_prompt_template,
    _substitute_command_template,
)


# ---------- strip ----------


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


# ---------- llm (zwingli action) + shared LLM call helper ----------


def _call_llm_with_profile(
    prompt: str,
    *,
    profile: TranscriptLLMProfileConfig | None,
    captures: Mapping[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    """Render the profile's user-prompt template against `prompt` and call the LLM.

    Returns (output_text, meta). `meta["template_applied"]` is set when the
    profile's user_prompt_template was used.
    """
    from voicepipe.zwingli import process_zwingli_prompt_result

    rendered_prompt = prompt
    template_applied = False
    if profile is not None and profile.user_prompt_template:
        rendered_prompt = _render_user_prompt_template(
            profile.user_prompt_template, text=prompt, captures=captures
        )
        template_applied = True

    if profile is not None:
        text, meta = process_zwingli_prompt_result(
            rendered_prompt,
            model=profile.model,
            temperature=profile.temperature,
            system_prompt=profile.system_prompt,
            user_prompt=profile.user_prompt,
        )
    else:
        text, meta = process_zwingli_prompt_result(rendered_prompt)

    if not isinstance(meta, dict):
        meta = {"meta": meta}
    else:
        meta = dict(meta)
    if template_applied:
        meta["template_applied"] = True
    return text, meta


def _action_zwingli(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del commands

    profile_name = ""
    if verb_cfg is not None:
        profile_name = (getattr(verb_cfg, "profile", "") or "").strip().lower()

    profile: TranscriptLLMProfileConfig | None = None
    if profile_name and profiles is not None:
        profile = profiles.get(profile_name)

    text, meta = _call_llm_with_profile(prompt, profile=profile, captures=captures)
    if profile_name:
        meta["profile_found"] = profile is not None
    return text, meta


# ---------- shell + execute (subprocess vs type-into-terminal) ----------


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


# ---------- clipboard (passthrough; emission layer copies) ----------


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


# ---------- codegen (LLM script → interpreter via tempfile) ----------


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

    Mirrors `_run_shell_command` but uses list-form argv (no shell expansion
    of the interpreter or path). Same VOICEPIPE_SHELL_ALLOW gate.
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
    `_action_zwingli`. The generated script is written to a tempfile and
    executed via `<interpreter> <tempfile>`. Honors `confirm`: when true,
    the *generated script* (not the user's phrase) is stashed for
    'zwingli yes' to run.
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


# ---------- type (keypress sequence parser) ----------


_TYPE_TOKEN_TRANSLATION = str.maketrans(
    {
        ",": " ",
        ":": " ",
        ";": " ",
        ".": " ",
        "!": " ",
        "?": " ",
        "(": " ",
        ")": " ",
        "[": " ",
        "]": " ",
        "{": " ",
        "}": " ",
        '"': " ",
        "'": " ",
        "\\": " ",
        "/": " ",
        "\t": " ",
        "\n": " ",
        "\r": " ",
    }
)

_TYPE_IGNORE_TOKENS = {
    "and",
    "then",
    "please",
    "a",
    "an",
    "the",
    "comma",
    "colon",
    "semicolon",
    "period",
    "full",
    "stop",
    "arrow",
    "key",
    "keys",
    "press",
    "pressed",
    "hit",
    "tap",
    "push",
    "hold",
    "release",
}

_TYPE_MOD_ALIASES = {
    "control": "ctrl",
    "ctrl": "ctrl",
    "ctl": "ctrl",
    "cntrl": "ctrl",
    "shift": "shift",
    "alt": "alt",
    "option": "alt",
    "meta": "meta",
    "super": "super",
    "win": "super",
    "windows": "super",
    "command": "cmd",
    "cmd": "cmd",
}

_TYPE_KEY_ALIASES = {
    "enter": "enter",
    "return": "enter",
    "submit": "enter",
    "send": "enter",
    "go": "enter",
    "newline": "enter",
    "linefeed": "enter",
    "linebreak": "enter",
    "tab": "tab",
    "escape": "esc",
    "esc": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "home": "home",
    "end": "end",
    "space": "space",
    "spacebar": "space",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "pgup": "pageup",
    "pgdn": "pagedown",
    # Common single-token variants.
    "uparrow": "up",
    "downarrow": "down",
    "leftarrow": "left",
    "rightarrow": "right",
}


def _tokenize_type_prompt(prompt: str) -> list[str]:
    cleaned = (prompt or "").strip().lower()
    if not cleaned:
        return []
    cleaned = cleaned.translate(_TYPE_TOKEN_TRANSLATION)
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    return [t for t in cleaned.split() if t]


def _parse_type_key(tokens: list[str], i: int) -> tuple[str | None, int]:
    if i < 0 or i >= len(tokens):
        return None, 1

    tok = tokens[i]
    if tok == "new" and i + 1 < len(tokens) and tokens[i + 1] == "line":
        return "enter", 2

    if tok == "line" and i + 1 < len(tokens) and tokens[i + 1] == "break":
        return "enter", 2

    if tok == "carriage" and i + 1 < len(tokens) and tokens[i + 1] == "return":
        return "enter", 2

    if tok == "cr" and i + 1 < len(tokens) and tokens[i + 1] == "lf":
        return "enter", 2

    if tok in ("up", "down", "left", "right"):
        return tok, 1

    if tok == "page" and i + 1 < len(tokens):
        nxt = tokens[i + 1]
        if nxt == "up":
            return "pageup", 2
        if nxt == "down":
            return "pagedown", 2

    if tok == "back" and i + 1 < len(tokens) and tokens[i + 1] == "space":
        return "backspace", 2

    if tok == "space" and i + 1 < len(tokens) and tokens[i + 1] == "bar":
        return "space", 2

    alias = _TYPE_KEY_ALIASES.get(tok)
    if alias is not None:
        return alias, 1

    if tok.startswith("f") and tok[1:].isdigit():
        try:
            n = int(tok[1:])
        except Exception:
            n = 0
        if 1 <= n <= 24:
            return f"f{n}", 1

    if len(tok) == 1 and tok.isalnum():
        return tok, 1

    return None, 1


def _flush_type_text(sequence: list[dict[str, Any]], pending: list[str]) -> None:
    if not pending:
        return
    text = " ".join(pending).strip()
    pending.clear()
    if text:
        sequence.append({"kind": "text", "text": text})


def _render_type_sequence(sequence: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in sequence:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if kind == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
            continue
        if kind == "key":
            key = str(item.get("key") or "").strip().lower()
            raw_mods = item.get("mods")
            mods: list[str] = []
            if isinstance(raw_mods, list):
                for m in raw_mods:
                    cleaned = str(m or "").strip().lower()
                    if cleaned:
                        mods.append(cleaned)
            if not key:
                continue
            if mods:
                parts.append("+".join([*mods, key]))
            else:
                parts.append(key)
    return " ".join(parts).strip()


def _action_type(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    """Type a sequence of keypresses and/or literal words.

    Example transcripts:
      - "up up up"
      - "up arrow up arrow"
      - "control b d"
    """
    del verb_cfg, profiles, captures, commands
    tokens = _tokenize_type_prompt(prompt)
    sequence: list[dict[str, Any]] = []
    pending_mods: list[str] = []
    pending_text: list[str] = []

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _TYPE_IGNORE_TOKENS:
            i += 1
            continue

        if "+" in tok:
            parts = [p for p in tok.split("+") if p]
            if parts:
                chord_mods: list[str] = []
                for part in parts[:-1]:
                    mapped = _TYPE_MOD_ALIASES.get(part)
                    if mapped:
                        chord_mods.append(mapped)
                    else:
                        pending_text.append(part)

                key_tok = parts[-1]
                key_id, _consumed = _parse_type_key([key_tok], 0)
                if key_id:
                    _flush_type_text(sequence, pending_text)
                    mods = [*pending_mods, *chord_mods]
                    pending_mods.clear()
                    sequence.append({"kind": "key", "key": key_id, "mods": mods})
                    i += 1
                    continue

        mapped_mod = _TYPE_MOD_ALIASES.get(tok)
        if mapped_mod:
            pending_mods.append(mapped_mod)
            i += 1
            continue

        key_id, consumed = _parse_type_key(tokens, i)
        if key_id:
            _flush_type_text(sequence, pending_text)
            mods = list(pending_mods)
            pending_mods.clear()
            sequence.append({"kind": "key", "key": key_id, "mods": mods})
            i += int(consumed)
            continue

        if pending_mods:
            pending_text.extend(pending_mods)
            pending_mods.clear()
        pending_text.append(tok)
        i += 1

    if pending_mods:
        pending_text.extend(pending_mods)
        pending_mods.clear()
    _flush_type_text(sequence, pending_text)

    out_text = _render_type_sequence(sequence)
    meta: dict[str, Any] = {"sequence": sequence}
    return out_text, meta


# ---------- plugin (user-supplied Python callable) ----------


_PLUGIN_PATH_CACHE: dict[str, tuple[int, Callable[[str], object]]] = {}
_PLUGIN_MODULE_CACHE: dict[tuple[str, str], Callable[[str], object]] = {}


def _resolve_plugin_path(path: str) -> Path:
    from voicepipe.config import config_dir

    base = config_dir(create=False).resolve(strict=False)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(base):
        raise RuntimeError(f"Plugin path must be inside the config dir: {base}")
    if resolved.suffix.lower() != ".py":
        raise RuntimeError(f"Plugin path must be a .py file: {resolved}")
    return resolved


def _load_callable_attr(obj: object, dotted_name: str) -> Callable[[str], object]:
    target: object = obj
    for part in (dotted_name or "").split("."):
        cleaned = part.strip()
        if not cleaned:
            continue
        target = getattr(target, cleaned)
    if not callable(target):
        raise RuntimeError(f"Plugin callable is not callable: {dotted_name!r}")
    return target  # type: ignore[return-value]


def _load_plugin_callable(plugin: TranscriptPluginConfig) -> Callable[[str], object]:
    callable_name = (plugin.callable or "").strip()
    if not callable_name:
        raise RuntimeError("Plugin verb is missing plugin.callable")

    if plugin.module:
        key = (plugin.module, callable_name)
        cached = _PLUGIN_MODULE_CACHE.get(key)
        if cached is not None:
            return cached
        module = importlib.import_module(plugin.module)
        fn = _load_callable_attr(module, callable_name)
        _PLUGIN_MODULE_CACHE[key] = fn
        return fn

    if plugin.path:
        resolved = _resolve_plugin_path(plugin.path)
        cache_key = str(resolved)
        try:
            st = resolved.stat()
        except FileNotFoundError as e:
            raise RuntimeError(f"Plugin file not found: {resolved}") from e
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        cached = _PLUGIN_PATH_CACHE.get(cache_key)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]

        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()[:12]
        module_name = f"voicepipe_user_plugin_{digest}_{mtime_ns}"
        spec = importlib.util.spec_from_file_location(module_name, resolved)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to load plugin module from: {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        fn = _load_callable_attr(module, callable_name)
        _PLUGIN_PATH_CACHE[cache_key] = (mtime_ns, fn)
        return fn

    raise RuntimeError("Plugin verb must set either plugin.module or plugin.path")


def _normalize_plugin_result(result: object) -> tuple[str, dict[str, Any]]:
    if isinstance(result, tuple) and len(result) == 2:
        raw_text, raw_meta = result
        if raw_text is None:
            out_text = ""
        elif isinstance(raw_text, bytes):
            out_text = raw_text.decode("utf-8", errors="replace")
        else:
            out_text = str(raw_text)
        out_meta = raw_meta if isinstance(raw_meta, dict) else {"meta": raw_meta}
        return out_text, dict(out_meta)

    if result is None:
        return "", {}
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace"), {}
    if isinstance(result, str):
        return result, {}
    return str(result), {}


def _action_plugin(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del profiles, captures, commands
    cleaned = (prompt or "").strip()
    plugin = getattr(verb_cfg, "plugin", None) if verb_cfg else None
    if plugin is None:
        raise RuntimeError("Plugin verb is missing configuration (plugin={...})")

    if (os.environ.get("VOICEPIPE_PLUGIN_ALLOW") or "").strip() != "1":
        raise RuntimeError(
            "Plugin verbs are disabled. Set VOICEPIPE_PLUGIN_ALLOW=1 to enable."
        )

    started = time.monotonic()
    fn = _load_plugin_callable(plugin)
    result = fn(cleaned)
    duration_ms = int((time.monotonic() - started) * 1000)
    out_text, plugin_meta = _normalize_plugin_result(result)
    meta: dict[str, Any] = {"duration_ms": duration_ms}
    if plugin_meta:
        meta["plugin_meta"] = plugin_meta
    return out_text, meta


# ---------- help (auto-injected; describes verbs) ----------


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


# ---------- yes / no (resume / cancel pending command) ----------


def _action_yes(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
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
        stdout, stderr, run_meta = _run_shell_command(entry.command, timeout_seconds=None)
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
        stdout, stderr, run_meta = _run_script_in_interpreter(
            interpreter, entry.command, timeout_seconds=None
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
) -> tuple[str, dict[str, Any]]:
    """Cancel a previously-stashed pending command. Args are ignored."""
    del prompt, verb_cfg, profiles, captures, commands
    from voicepipe import pending as pending_mod

    entry = pending_mod.load_pending()
    pending_mod.clear_pending()
    if entry is None:
        return ("No pending command to cancel.", {"no_pending": True})
    return (
        f"Cancelled pending {entry.verb_type}: {entry.command}",
        {"cancelled": True, "pending_verb": entry.verb, "pending_verb_type": entry.verb_type},
    )


# ---------- action dispatch table ----------


ActionHandler = Callable[..., tuple[str, dict[str, Any]]]

_ACTIONS: dict[str, ActionHandler] = {
    "strip": _action_strip,
    "zwingli": _action_zwingli,
    "shell": _action_shell,
    "execute": _action_execute,
    "type": _action_type,
    "plugin": _action_plugin,
    "clipboard": _action_clipboard,
    "codegen": _action_codegen,
    "help": _action_help,
    "yes": _action_yes,
    "no": _action_no,
}

# Keys returned by handlers in their inner_meta that the dispatcher should
# surface at the top level of verb metadata rather than under "handler_meta".
_PROMOTED_META_KEYS: tuple[str, ...] = ("profile_found", "template_applied")
