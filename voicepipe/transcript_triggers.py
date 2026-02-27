from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
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
    get_transcript_commands_config,
)


@dataclass(frozen=True)
class TranscriptTriggerMatch:
    trigger: str
    action: str
    remainder: str
    reason: str


_ZWINGLI_DEBUG_LOG_MAX_BYTES = 20 * 1024 * 1024


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
    if size <= _ZWINGLI_DEBUG_LOG_MAX_BYTES:
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


def _action_strip(prompt: str) -> tuple[str, dict[str, Any]]:
    return (prompt or "").strip(), {}


def _action_zwingli(prompt: str) -> tuple[str, dict[str, Any]]:
    from voicepipe.zwingli import process_zwingli_prompt_result

    text, meta = process_zwingli_prompt_result(prompt)
    if not isinstance(meta, dict):
        meta = {"meta": meta}
    return text, meta


def _render_user_prompt_template(template: str, *, text: str) -> str:
    cleaned_template = (template or "").strip()
    cleaned_text = (text or "").strip()
    if not cleaned_template:
        return cleaned_text

    if "{{text}}" in cleaned_template:
        return cleaned_template.replace("{{text}}", cleaned_text)

    if cleaned_text:
        return cleaned_template.rstrip() + "\n\n" + cleaned_text

    return cleaned_template


def _action_zwingli_profile(
    prompt: str, *, profile: TranscriptLLMProfileConfig
) -> tuple[str, dict[str, Any]]:
    from voicepipe.zwingli import process_zwingli_prompt_result

    text, meta = process_zwingli_prompt_result(
        prompt,
        model=profile.model,
        temperature=profile.temperature,
        system_prompt=profile.system_prompt,
        user_prompt=profile.user_prompt,
    )
    if not isinstance(meta, dict):
        meta = {"meta": meta}
    return text, meta


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


def _action_shell(prompt: str, *, timeout_seconds: float | None = None) -> tuple[str, dict[str, Any]]:
    cleaned = (prompt or "").strip()
    if not cleaned:
        return "", {"returncode": 0, "duration_ms": 0}

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
        output = stdout if stdout.strip() else stderr
        output = output.rstrip("\n")

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
        return output, meta
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

        output = stdout if str(stdout).strip() else str(stderr)
        output = str(output).rstrip("\n")

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
        return output, meta


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


def _action_plugin(prompt: str, *, verb_cfg: TranscriptVerbConfig) -> tuple[str, dict[str, Any]]:
    cleaned = (prompt or "").strip()
    plugin = getattr(verb_cfg, "plugin", None)
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


_ACTIONS: dict[str, Callable[[str], tuple[str, dict[str, Any]]]] = {
    "strip": _action_strip,
    "zwingli": _action_zwingli,
    "shell": _action_shell,
}

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


def _dispatch_prompt(prompt: str, *, commands: TranscriptCommandsConfig) -> tuple[str, dict[str, Any]]:
    cleaned = (prompt or "").strip()
    verb, args = _split_dispatch_verb(cleaned)

    # Normalize common STT variants that turn a single-word verb into two words.
    # Example: "plugin" -> "plug in".
    if verb == "plug" and "plugin" in commands.verbs and "plug" not in commands.verbs:
        stripped_args = args.lstrip()
        lowered_args = stripped_args.lower()
        if lowered_args == "in":
            args = ""
            verb = "plugin"
        elif lowered_args.startswith("in"):
            next_ch = stripped_args[2:3]
            if not next_ch:
                args = ""
                verb = "plugin"
            elif next_ch.isspace() or next_ch in _DISPATCH_SEPARATORS:
                j = 2
                if j < len(stripped_args) and stripped_args[j] in _DISPATCH_SEPARATORS:
                    j += 1
                while j < len(stripped_args) and stripped_args[j].isspace():
                    j += 1
                args = stripped_args[j:]
                verb = "plugin"

    verb_cfg = commands.verbs.get(verb) if verb else None

    if verb_cfg is not None and bool(verb_cfg.enabled):
        action = _resolve_action_from_verb_config(verb, verb_cfg)

        inner_meta: dict[str, Any]
        profile_found = False
        template_applied = False
        profile_name = (getattr(verb_cfg, "profile", None) or "").strip().lower()
        profile = commands.llm_profiles.get(profile_name) if profile_name else None
        if action == "plugin":
            out_text, inner_meta = _action_plugin(args, verb_cfg=verb_cfg)
        elif action == "zwingli" and profile is not None:
            profile_found = True
            profile_prompt = args
            if profile.user_prompt_template:
                profile_prompt = _render_user_prompt_template(
                    profile.user_prompt_template, text=args
                )
                template_applied = True
            out_text, inner_meta = _action_zwingli_profile(profile_prompt, profile=profile)
        elif action == "shell":
            out_text, inner_meta = _action_shell(args, timeout_seconds=verb_cfg.timeout_seconds)
        else:
            handler = _ACTIONS.get(action)
            if handler is None:
                raise RuntimeError(f"Unknown verb action: {action!r} (verb={verb!r})")
            out_text, inner_meta = handler(args)

        meta: dict[str, Any] = {
            "mode": "verb",
            "verb": verb,
            "verb_type": getattr(verb_cfg, "type", None),
            "action": action,
        }
        if getattr(verb_cfg, "destination", None):
            meta["destination"] = verb_cfg.destination
        if getattr(verb_cfg, "profile", None):
            meta["profile"] = verb_cfg.profile
            if action == "zwingli":
                meta["profile_found"] = profile_found
            if template_applied:
                meta["template_applied"] = True
        if getattr(verb_cfg, "timeout_seconds", None) is not None:
            meta["timeout_seconds"] = verb_cfg.timeout_seconds
        plugin = getattr(verb_cfg, "plugin", None)
        if plugin is not None:
            meta["plugin"] = {
                "module": plugin.module,
                "path": plugin.path,
                "callable": plugin.callable,
            }
        if inner_meta:
            meta["handler_meta"] = inner_meta
        return out_text, meta

    unknown_action = (commands.dispatch.unknown_verb or "").strip().lower() or "strip"
    handler = _ACTIONS.get(unknown_action)
    if handler is None:
        raise RuntimeError(f"Unknown dispatch.unknown_verb action: {unknown_action!r}")
    out_text, inner_meta = handler(cleaned)
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


def apply_transcript_triggers(
    text: str,
    *,
    commands: TranscriptCommandsConfig | None = None,
    triggers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Apply a configured transcript trigger, returning (output_text, metadata).

    If no trigger matches, this returns the original text and `None` metadata.
    """
    resolved_commands: TranscriptCommandsConfig
    if commands is not None:
        resolved_commands = commands
    elif triggers is not None:
        resolved_commands = _default_commands_for_triggers(triggers)
    else:
        resolved_commands = get_transcript_commands_config(load_env=False)

    match = match_transcript_trigger(text, triggers=resolved_commands.triggers)
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
        try:
            output_text, meta = _dispatch_prompt(match.remainder, commands=resolved_commands)
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
            return match.remainder, {
                "ok": False,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "error": str(e),
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
        return match.remainder, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": f"Unknown transcript trigger action: {match.action!r}",
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
        return match.remainder, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": str(e),
        }
