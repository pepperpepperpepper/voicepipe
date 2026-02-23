from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import subprocess
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

        if lowered.startswith(trigger + " "):
            return TranscriptTriggerMatch(
                trigger=trigger,
                action=action,
                remainder=cleaned[len(trigger) :].lstrip(),
                reason="prefix:space",
            )

        for sep in (",", ":", ";", "."):
            if lowered.startswith(trigger + sep):
                return TranscriptTriggerMatch(
                    trigger=trigger,
                    action=action,
                    remainder=cleaned[len(trigger) + 1 :].lstrip(),
                    reason=f"prefix:{sep}",
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
        raise RuntimeError(
            "Shell trigger action is disabled. Set VOICEPIPE_SHELL_ALLOW=1 to enable."
        )

    timeout_s = _resolve_shell_timeout_seconds(timeout_seconds=timeout_seconds)

    started = time.monotonic()
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
            return output_text, payload
        except Exception as e:
            return match.remainder, {
                "ok": False,
                "trigger": match.trigger,
                "action": match.action,
                "reason": match.reason,
                "error": str(e),
            }

    handler = _ACTIONS.get(match.action)
    if handler is None:
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
        return output_text, payload
    except Exception as e:
        return match.remainder, {
            "ok": False,
            "trigger": match.trigger,
            "action": match.action,
            "reason": match.reason,
            "error": str(e),
        }
