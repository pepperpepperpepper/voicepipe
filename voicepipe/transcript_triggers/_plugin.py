"""``plugin`` action: invoke a user-supplied Python callable.

A verb of ``type: "plugin"`` names a callable via either ``module`` (an
importable dotted name) or ``path`` (a ``.py`` file under the voicepipe
config dir). The callable receives the user's spoken args string and
returns a string, bytes, or ``(text, meta)`` tuple. Gated on
``VOICEPIPE_PLUGIN_ALLOW=1``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptPluginConfig,
    TranscriptVerbConfig,
)

from ._actuator import Actuator


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
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del profiles, captures, commands, actuator
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
