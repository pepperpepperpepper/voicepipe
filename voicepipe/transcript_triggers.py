from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptPluginConfig,
    TranscriptVerbConfig,
    get_transcript_triggers,
    get_transcript_commands_config,
)


@dataclass(frozen=True)
class TranscriptTriggerMatch:
    trigger: str
    action: str
    remainder: str
    reason: str


_ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES = 20 * 1024 * 1024


def _zwingli_debug_log_max_bytes() -> int:
    """Resolve the debug-log rotation threshold from the environment.

    Accepts raw bytes ("1048576") or a K/M/G suffix ("20M", "1.5G"). A value
    of 0 disables rotation (file grows without bound). Empty, malformed, or
    negative values fall back to the default.
    """
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES") or "").strip()
    if not raw:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES

    multiplier = 1
    suffix = raw[-1:].lower()
    if suffix in ("k", "m", "g"):
        multiplier = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[suffix]
        raw = raw[:-1].strip()

    try:
        value = float(raw)
    except ValueError:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES
    if value < 0:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES
    return int(value * multiplier)


def _zwingli_debug_log_enabled() -> bool:
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG") or "").strip().lower()
    if raw in {"0", "false", "f", "no", "n", "off"}:
        return False
    return True


def _zwingli_debug_log_path() -> Path:
    override = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE") or "").strip()
    if override:
        try:
            return Path(override).expanduser()
        except Exception:
            return Path(override)
    if os.name != "nt":
        return Path("/tmp/zwingli-debug.log")
    try:
        return Path(tempfile.gettempdir()) / "zwingli-debug.log"
    except Exception:
        return Path("zwingli-debug.log")


def _truncate_for_log(value: object, *, max_chars: int = 20_000) -> object:
    if not isinstance(value, str):
        return value
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _maybe_rotate_debug_log(path: Path) -> None:
    max_bytes = _zwingli_debug_log_max_bytes()
    if max_bytes <= 0:
        return

    try:
        st = path.stat()
    except FileNotFoundError:
        return
    except Exception:
        return

    try:
        size = int(getattr(st, "st_size", 0) or 0)
    except Exception:
        size = 0
    if size <= max_bytes:
        return

    backup = Path(str(path) + ".1")
    try:
        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass
        os.replace(path, backup)
    except Exception:
        # If rotation fails, carry on; logging should never break core behavior.
        return


def _write_zwingli_debug_event(event: dict[str, Any]) -> None:
    if not _zwingli_debug_log_enabled():
        return

    payload = dict(event)
    payload.setdefault("ts_ms", int(time.time() * 1000))
    payload.setdefault("pid", int(os.getpid()))

    # Keep the log usable when commands produce large output.
    for key in ("text", "remainder", "prompt", "args", "command", "stdout", "stderr", "output_text", "error"):
        if key in payload:
            payload[key] = _truncate_for_log(payload[key])

    try:
        path = _zwingli_debug_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        _maybe_rotate_debug_log(path)

        line = json.dumps(payload, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


_ZWINGLI_ERROR_PREFIX = "⚠ zwingli"
_ERROR_DESTINATION_FALLBACK = "type"
_ERROR_DESTINATION_VALID = frozenset({"type", "clipboard", "both"})


def _format_zwingli_error_text(reason: str) -> str:
    cleaned = (reason or "").strip()
    return f"{_ZWINGLI_ERROR_PREFIX}: {cleaned}" if cleaned else f"{_ZWINGLI_ERROR_PREFIX} error"


def _resolve_error_destination(commands: TranscriptCommandsConfig | None) -> str:
    """Read dispatch.error_destination; fall back to 'type' on any issue."""
    if commands is None:
        try:
            commands = get_transcript_commands_config(load_env=False)
        except Exception:
            return _ERROR_DESTINATION_FALLBACK
    raw = (getattr(commands.dispatch, "error_destination", None) or _ERROR_DESTINATION_FALLBACK)
    cleaned = raw.strip().lower() if isinstance(raw, str) else _ERROR_DESTINATION_FALLBACK
    return cleaned if cleaned in _ERROR_DESTINATION_VALID else _ERROR_DESTINATION_FALLBACK


def _apply_error_destination(
    reason: str, *, commands: TranscriptCommandsConfig | None
) -> tuple[str, dict[str, Any]]:
    """Format the error and route it per dispatch.error_destination.

    Returns (output_text, meta_extras). The output_text is what callers should
    type/echo; meta_extras carries suppress_type and clipboard flags so the
    same downstream wiring used by the clipboard verb picks this up.
    """
    error_text = _format_zwingli_error_text(reason)
    destination = _resolve_error_destination(commands)
    extras: dict[str, Any] = {"error_destination": destination}

    if destination in ("clipboard", "both"):
        try:
            from voicepipe.clipboard import copy_to_clipboard

            ok, _err = copy_to_clipboard(error_text)
            extras["clipboard"] = bool(ok)
        except Exception:
            extras["clipboard"] = False

    if destination == "clipboard":
        extras["suppress_type"] = True

    return error_text, extras


def match_transcript_trigger(
    text: str,
    *,
    triggers: Mapping[str, str],
) -> TranscriptTriggerMatch | None:
    """Match a configured trigger prefix against transcript text.

    This is intentionally lightweight (string checks only). It is not an audio
    wake word; it operates purely on the transcription output.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None

    lowered = cleaned.lower()

    word_separators: tuple[tuple[str, str], ...] = (
        ("comma", ","),
        ("colon", ":"),
        ("semicolon", ";"),
        ("semi colon", ";"),
        ("period", "."),
        ("full stop", "."),
    )

    for raw_trigger, raw_action in triggers.items():
        trigger = (raw_trigger or "").strip().lower()
        if not trigger:
            continue
        action = (raw_action or "").strip().lower() or "strip"

        if lowered == trigger:
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder="",
                reason="exact",
            )

        if not lowered.startswith(trigger):
            continue

        after = len(trigger)
        if after >= len(lowered):
            continue

        # Boundary-aware match: allow either whitespace or a separator after
        # the trigger. Prefer stripping a separator even when there's whitespace
        # before it (e.g. "zwingli , do it").
        i = after
        while i < len(lowered) and lowered[i].isspace():
            i += 1

        if i < len(lowered):
            # Trigger followed by a separator character.
            for sep in (",", ":", ";", "."):
                if lowered[i] == sep:
                    return TranscriptTriggerMatch(
                        trigger=trigger,
                        action=action,
                        remainder=cleaned[i + 1 :].lstrip(),
                        reason=f"prefix:{sep}",
                    )

            # Trigger followed by a separator word (e.g. "zwingli comma ...").
            for word, sep in word_separators:
                if not lowered.startswith(word, i):
                    continue
                end = i + len(word)
                if end < len(lowered):
                    next_ch = lowered[end]
                    if not (next_ch.isspace() or next_ch in {",", ":", ";", "."}):
                        continue
                j = end
                while j < len(lowered) and lowered[j].isspace():
                    j += 1
                if j < len(lowered) and lowered[j] in {",", ":", ";", "."}:
                    j += 1
                return TranscriptTriggerMatch(
                    trigger=trigger,
                    action=action,
                    remainder=cleaned[j:].lstrip(),
                    reason=f"prefix:{sep}",
                )

        # Trigger followed by whitespace and then non-separator content.
        if lowered[after].isspace():
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder=cleaned[after:].lstrip(),
                reason="prefix:space",
            )

    return None


def _action_strip(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures
    return (prompt or "").strip(), {}


def _action_zwingli(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    from voicepipe.zwingli import process_zwingli_prompt_result

    profile_name = ""
    if verb_cfg is not None:
        profile_name = (getattr(verb_cfg, "profile", "") or "").strip().lower()

    profile: TranscriptLLMProfileConfig | None = None
    if profile_name and profiles is not None:
        profile = profiles.get(profile_name)

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

    if profile_name:
        meta["profile_found"] = profile is not None
    if template_applied:
        meta["template_applied"] = True
    return text, meta


_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def _render_user_prompt_template(
    template: str,
    *,
    text: str,
    captures: Mapping[str, str] | None = None,
) -> str:
    cleaned_template = (template or "").strip()
    cleaned_text = (text or "").strip()
    if not cleaned_template:
        return cleaned_text

    substitutions: dict[str, str] = {"text": cleaned_text}
    if captures:
        for name, value in captures.items():
            substitutions[name] = "" if value is None else str(value)

    used_text = False

    def _resolve(match: re.Match[str]) -> str:
        nonlocal used_text
        name = match.group(1)
        if name == "text":
            used_text = True
        if name in substitutions:
            return substitutions[name]
        return match.group(0)

    rendered = _TEMPLATE_PLACEHOLDER_RE.sub(_resolve, cleaned_template)

    if not used_text and cleaned_text:
        return rendered.rstrip() + "\n\n" + cleaned_text
    return rendered


_PATTERN_COMPILE_CACHE: dict[str, tuple["re.Pattern[str]", tuple[str, ...]]] = {}


def _compile_verb_pattern(pattern: str) -> tuple["re.Pattern[str]", tuple[str, ...]]:
    """Compile a verb pattern into a regex + capture-name tuple.

    Pattern syntax: literal text + ``{name}`` placeholders. Literals match
    case-insensitively with flexible whitespace; placeholders capture
    non-empty content up to the next literal or end of input. The compiled
    regex anchors on the whole input.
    """
    cached = _PATTERN_COMPILE_CACHE.get(pattern)
    if cached is not None:
        return cached

    placeholder_re = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
    parts: list[str] = []
    names: list[str] = []
    last = 0
    for m in placeholder_re.finditer(pattern):
        literal = pattern[last : m.start()]
        if literal.strip():
            parts.append(r"\s+".join(re.escape(w) for w in literal.split()))
        name = m.group(1)
        if name in names:
            raise ValueError(f"Duplicate capture name {name!r} in pattern {pattern!r}")
        names.append(name)
        # Require at least one non-whitespace char so empty/whitespace captures
        # don't match.
        parts.append(rf"(?P<{name}>\S(?:.*?\S)?)")
        last = m.end()
    trailing = pattern[last:]
    if trailing.strip():
        parts.append(r"\s+".join(re.escape(w) for w in trailing.split()))

    body = r"\s*".join(parts) if parts else ""
    compiled = re.compile(rf"^\s*{body}\s*$", re.IGNORECASE)
    result = (compiled, tuple(names))
    _PATTERN_COMPILE_CACHE[pattern] = result
    return result


def _find_pattern_match(
    chunk: str, *, commands: TranscriptCommandsConfig
) -> tuple[str, dict[str, str]] | None:
    """Return (verb_name, captures) for the first enabled verb whose pattern
    matches the chunk, or None when no pattern matches.

    Iteration order follows the verbs dict (insertion order). Disabled verbs
    are skipped. Verbs without a pattern are skipped.
    """
    text = (chunk or "").strip()
    if not text:
        return None
    for verb_name, verb_cfg in commands.verbs.items():
        if not bool(verb_cfg.enabled):
            continue
        pattern = getattr(verb_cfg, "pattern", None)
        if not pattern:
            continue
        try:
            compiled, _names = _compile_verb_pattern(pattern)
        except Exception:
            continue
        m = compiled.match(text)
        if m is None:
            continue
        captures: dict[str, str] = {}
        for name, value in m.groupdict().items():
            captures[name] = "" if value is None else value.strip()
        return verb_name, captures
    return None


def _substitute_command_template(template: str, captures: Mapping[str, str]) -> str:
    """Replace ``{name}`` tokens in a shell command_template with captures."""
    placeholder_re = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def _resolve(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in captures:
            return str(captures[name])
        return match.group(0)

    return placeholder_re.sub(_resolve, template)


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
) -> tuple[str, dict[str, Any]]:
    del profiles
    timeout_seconds = getattr(verb_cfg, "timeout_seconds", None) if verb_cfg else None
    command_template = getattr(verb_cfg, "command_template", None) if verb_cfg else None
    if command_template and captures is not None:
        command = _substitute_command_template(command_template, captures)
    else:
        command = prompt
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
) -> tuple[str, dict[str, Any]]:
    """Prepare a shell command for *typing* into a terminal and pressing Enter.

    This action must never spawn a subprocess to run the command; it only
    returns the cleaned command text and metadata indicating that an Enter
    keystroke should be sent by the caller when typing is the destination.
    """
    del profiles
    command_template = getattr(verb_cfg, "command_template", None) if verb_cfg else None
    if command_template and captures is not None:
        source = _substitute_command_template(command_template, captures)
    else:
        source = prompt
    cleaned = _strip_trailing_sentence_punct_from_shell_command(source)
    cleaned = (cleaned or "").strip()
    if not cleaned:
        return "", {"enter": False}
    return cleaned, {"enter": True}


def _action_clipboard(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Passthrough handler: the actual clipboard copy is performed by the
    emission layer via verb destination routing (see verb_cfg.destination)."""
    del verb_cfg, profiles, captures
    return (prompt or "").strip(), {}


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
) -> tuple[str, dict[str, Any]]:
    """Type a sequence of keypresses and/or literal words.

    Example transcripts:
      - "up up up"
      - "up arrow up arrow"
      - "control b d"
    """
    del verb_cfg, profiles, captures
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
) -> tuple[str, dict[str, Any]]:
    del profiles, captures
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


ActionHandler = Callable[..., tuple[str, dict[str, Any]]]

_ACTIONS: dict[str, ActionHandler] = {
    "strip": _action_strip,
    "zwingli": _action_zwingli,
    "shell": _action_shell,
    "execute": _action_execute,
    "type": _action_type,
    "plugin": _action_plugin,
    "clipboard": _action_clipboard,
}

# Keys returned by handlers in their inner_meta that the dispatcher should
# surface at the top level of verb metadata rather than under "handler_meta".
_PROMOTED_META_KEYS: tuple[str, ...] = ("profile_found", "template_applied")

_DISPATCH_SEPARATORS = (",", ":", ";", ".")


def _split_dispatch_verb(prompt: str) -> tuple[str, str]:
    cleaned = (prompt or "").strip()
    if not cleaned:
        return "", ""

    i = 0
    while i < len(cleaned) and not cleaned[i].isspace() and cleaned[i] not in _DISPATCH_SEPARATORS:
        i += 1

    verb = cleaned[:i].strip().lower()
    j = i
    if j < len(cleaned) and cleaned[j] in _DISPATCH_SEPARATORS:
        j += 1
    while j < len(cleaned) and cleaned[j].isspace():
        j += 1
    args = cleaned[j:]
    return verb, args


def _default_commands_for_triggers(triggers: Mapping[str, str]) -> TranscriptCommandsConfig:
    return TranscriptCommandsConfig(triggers=dict(triggers))


def _resolve_action_from_verb_config(_verb: str, cfg: TranscriptVerbConfig) -> str:
    del _verb
    return (cfg.action or "").strip().lower() or "strip"


def _build_verb_alias_map(
    verbs: Mapping[str, TranscriptVerbConfig],
) -> dict[str, str]:
    """Return a phrase -> canonical-verb map built from each verb's aliases.

    Aliases that collide with an existing verb name or with another alias
    are skipped (first-write wins) to keep verb resolution deterministic.
    """
    out: dict[str, str] = {}
    for verb, cfg in verbs.items():
        for alias in getattr(cfg, "aliases", ()) or ():
            phrase = " ".join((alias or "").strip().lower().split())
            if not phrase or phrase == verb or phrase in verbs:
                continue
            out.setdefault(phrase, verb)
    return out


def _resolve_verb_and_args(
    cleaned: str, *, commands: TranscriptCommandsConfig
) -> tuple[str, str]:
    """Split a post-trigger prompt into (verb, args), honoring verb aliases."""
    if not cleaned:
        return "", ""

    alias_map = _build_verb_alias_map(commands.verbs)

    if alias_map:
        lowered = cleaned.lower()
        # Try multi-word aliases first, longest match wins.
        for alias in sorted(
            (a for a in alias_map if " " in a), key=lambda a: -len(a)
        ):
            if lowered == alias:
                return alias_map[alias], ""
            if not lowered.startswith(alias):
                continue
            tail_idx = len(alias)
            tail_ch = cleaned[tail_idx]
            if not (tail_ch.isspace() or tail_ch in _DISPATCH_SEPARATORS):
                continue
            j = tail_idx
            if cleaned[j] in _DISPATCH_SEPARATORS:
                j += 1
            while j < len(cleaned) and cleaned[j].isspace():
                j += 1
            if j < len(cleaned) and cleaned[j] in _DISPATCH_SEPARATORS:
                j += 1
            while j < len(cleaned) and cleaned[j].isspace():
                j += 1
            return alias_map[alias], cleaned[j:]

    verb, args = _split_dispatch_verb(cleaned)
    if verb and verb in alias_map:
        return alias_map[verb], args
    return verb, args


def _invoke_verb_handler(
    verb: str,
    verb_cfg: TranscriptVerbConfig,
    args: str,
    *,
    commands: TranscriptCommandsConfig,
    captures: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run a verb's action handler and build its top-level meta payload."""
    action = _resolve_action_from_verb_config(verb, verb_cfg)
    handler = _ACTIONS.get(action)
    if handler is None:
        raise RuntimeError(f"Unknown verb action: {action!r} (verb={verb!r})")
    out_text, inner_meta = handler(
        args,
        verb_cfg=verb_cfg,
        profiles=commands.llm_profiles,
        captures=captures,
    )

    meta: dict[str, Any] = {
        "mode": "verb",
        "verb": verb,
        "verb_type": getattr(verb_cfg, "type", None),
        "action": action,
    }
    if captures:
        meta["captures"] = dict(captures)
    destination = getattr(verb_cfg, "destination", None)
    if not destination and action == "clipboard":
        destination = "clipboard"
    if destination:
        meta["destination"] = destination
    if getattr(verb_cfg, "profile", None):
        meta["profile"] = verb_cfg.profile
    if getattr(verb_cfg, "timeout_seconds", None) is not None:
        meta["timeout_seconds"] = verb_cfg.timeout_seconds
    plugin = getattr(verb_cfg, "plugin", None)
    if plugin is not None:
        meta["plugin"] = {
            "module": plugin.module,
            "path": plugin.path,
            "callable": plugin.callable,
        }
    for key in _PROMOTED_META_KEYS:
        if key in inner_meta:
            meta[key] = inner_meta.pop(key)
    if inner_meta:
        meta["handler_meta"] = inner_meta
    return out_text, meta


def _dispatch_single_step(
    verb: str,
    args: str,
    raw_chunk: str,
    *,
    commands: TranscriptCommandsConfig,
) -> tuple[str, dict[str, Any]]:
    """Run one dispatch step. `raw_chunk` is the original chunk before verb
    extraction; it is used both for pattern matching against the whole chunk
    and as input to the unknown-verb fallback handler so the verb token isn't
    silently dropped.
    """
    pattern_match = _find_pattern_match(raw_chunk, commands=commands)
    if pattern_match is not None:
        pattern_verb, captures = pattern_match
        pattern_verb_cfg = commands.verbs[pattern_verb]
        return _invoke_verb_handler(
            pattern_verb,
            pattern_verb_cfg,
            raw_chunk,
            commands=commands,
            captures=captures,
        )

    verb_cfg = commands.verbs.get(verb) if verb else None

    if verb_cfg is not None and bool(verb_cfg.enabled):
        return _invoke_verb_handler(verb, verb_cfg, args, commands=commands)

    unknown_action = (commands.dispatch.unknown_verb or "").strip().lower() or "strip"
    handler = _ACTIONS.get(unknown_action)
    if handler is None:
        raise RuntimeError(f"Unknown dispatch.unknown_verb action: {unknown_action!r}")
    out_text, inner_meta = handler(
        raw_chunk, verb_cfg=None, profiles=commands.llm_profiles, captures=None
    )
    meta: dict[str, Any] = {
        "mode": "unknown-verb",
        "verb": verb,
        "action": unknown_action,
    }
    if verb_cfg is not None and not bool(verb_cfg.enabled):
        meta["disabled_verb"] = verb
    if inner_meta:
        meta["handler_meta"] = inner_meta
    return out_text, meta


_CHAIN_KEYWORD = " then "


def _find_chain_boundaries(
    text: str, *, commands: TranscriptCommandsConfig
) -> list[tuple[int, int]]:
    """Locate ' then <verb>' boundaries where <verb> resolves via the verbs map.

    Returns a list of (split_start, next_chunk_start) byte positions. Empty
    list means no chain.
    """
    out: list[tuple[int, int]] = []
    lowered = text.lower()
    search_start = 0
    n = len(text)
    while True:
        idx = lowered.find(_CHAIN_KEYWORD, search_start)
        if idx == -1:
            return out
        after = idx + len(_CHAIN_KEYWORD)
        # Skip any extra whitespace before the candidate verb token.
        while after < n and text[after].isspace():
            after += 1
        if after >= n:
            return out
        candidate = text[after:]
        candidate_verb, _ = _resolve_verb_and_args(candidate, commands=commands)
        if candidate_verb and candidate_verb in commands.verbs:
            out.append((idx, after))
            search_start = after
        else:
            search_start = idx + 1


def _split_chain_chunks(
    cleaned: str, *, commands: TranscriptCommandsConfig
) -> list[str]:
    """Split the post-trigger prompt into chain chunks. Single chunk = no chain."""
    boundaries = _find_chain_boundaries(cleaned, commands=commands)
    if not boundaries:
        return [cleaned]
    chunks: list[str] = []
    last = 0
    for split_start, next_start in boundaries:
        chunks.append(cleaned[last:split_start].strip())
        last = next_start
    chunks.append(cleaned[last:].strip())
    return chunks


def _dispatch_prompt(prompt: str, *, commands: TranscriptCommandsConfig) -> tuple[str, dict[str, Any]]:
    cleaned = (prompt or "").strip()
    chunks = _split_chain_chunks(cleaned, commands=commands)

    if len(chunks) == 1:
        verb, args = _resolve_verb_and_args(chunks[0], commands=commands)
        return _dispatch_single_step(verb, args, chunks[0], commands=commands)

    chain_metas: list[dict[str, Any]] = []
    prior_output = ""
    final_text = ""
    final_meta: dict[str, Any] = {}

    for i, chunk in enumerate(chunks):
        verb, split_args = _resolve_verb_and_args(chunk, commands=commands)
        if i == 0:
            step_input = split_args
            step_chunk = chunk
        elif split_args.strip():
            # Explicit args after the chain verb: honor them, ignore prior output.
            step_input = split_args
            step_chunk = chunk
        else:
            # Verb-only chain step: pipe the previous output in.
            step_input = prior_output
            step_chunk = prior_output

        step_text, step_meta = _dispatch_single_step(
            verb, step_input, step_chunk, commands=commands
        )
        prior_output = step_text

        if i < len(chunks) - 1:
            chain_metas.append(step_meta)
        else:
            final_text = step_text
            final_meta = step_meta

    final_meta["chain"] = chain_metas
    return final_text, final_meta


def apply_transcript_triggers(
    text: str,
    *,
    commands: TranscriptCommandsConfig | None = None,
    triggers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Apply a configured transcript trigger, returning (output_text, metadata).

    If no trigger matches, this returns the original text and `None` metadata.
    """
    resolved_triggers: Mapping[str, str]
    resolved_commands: TranscriptCommandsConfig | None = None

    if commands is not None:
        resolved_commands = commands
        resolved_triggers = commands.triggers
    elif triggers is not None:
        resolved_triggers = triggers
    else:
        # Lightweight hot path: load trigger prefixes only. Full verbs/profiles
        # config is loaded lazily only if a trigger matches and requests it
        # (e.g. action=dispatch).
        resolved_triggers = get_transcript_triggers(load_env=False)

    match = match_transcript_trigger(text, triggers=resolved_triggers)
    if match is None:
        return text, None

    _write_zwingli_debug_event(
        {
            "event": "trigger_match",
            "text": (text or ""),
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "remainder": match.remainder,
        }
    )

    if match.action == "dispatch":
        if resolved_commands is None:
            if triggers is not None:
                resolved_commands = _default_commands_for_triggers(resolved_triggers)
            else:
                resolved_commands = get_transcript_commands_config(load_env=False)

        try:
            output_text, meta = _dispatch_prompt(
                match.remainder,
                commands=resolved_commands,
            )
            payload: dict[str, Any] = {
                "ok": True,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
            }
            if meta:
                payload["meta"] = meta
            _write_zwingli_debug_event(
                {
                    "event": "dispatch_ok",
                    "trigger": match.trigger,
                    "reason": match.reason,
                    "remainder": match.remainder,
                    "output_text": output_text,
                    "meta": meta,
                }
            )
            return output_text, payload
        except Exception as e:
            _write_zwingli_debug_event(
                {
                    "event": "dispatch_error",
                    "trigger": match.trigger,
                    "reason": match.reason,
                    "remainder": match.remainder,
                    "error": str(e),
                }
            )
            error_text, error_meta = _apply_error_destination(
                str(e), commands=resolved_commands
            )
            return error_text, {
                "ok": False,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "error": str(e),
                "meta": error_meta,
            }

    handler = _ACTIONS.get(match.action)
    if handler is None:
        _write_zwingli_debug_event(
            {
                "event": "action_missing",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
            }
        )
        error_msg = f"Unknown transcript trigger action: {match.action!r}"
        error_text, error_meta = _apply_error_destination(error_msg, commands=resolved_commands)
        return error_text, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": error_msg,
            "meta": error_meta,
        }

    try:
        output_text, meta = handler(match.remainder)
        payload: dict[str, Any] = {
            "ok": True,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
        }
        if meta:
            payload["meta"] = meta
        _write_zwingli_debug_event(
            {
                "event": "action_ok",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
                "output_text": output_text,
                "meta": meta,
            }
        )
        return output_text, payload
    except Exception as e:
        _write_zwingli_debug_event(
            {
                "event": "action_error",
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "remainder": match.remainder,
                "error": str(e),
            }
        )
        error_text, error_meta = _apply_error_destination(str(e), commands=resolved_commands)
        return error_text, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": str(e),
            "meta": error_meta,
        }
