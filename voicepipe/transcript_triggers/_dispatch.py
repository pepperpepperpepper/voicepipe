"""Verb resolution + chain splitting + the dispatcher loop.

The flow per chunk:
    pattern match (regex) → alias lookup → direct verb lookup → unknown-verb fallback
The chain splitter divides the post-trigger remainder on ` then ` boundaries
where the token after `then` resolves to a real verb (otherwise it's just
the word "then" appearing in dictation).
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import TranscriptCommandsConfig, TranscriptVerbConfig

from ._actions import _ACTIONS, _PROMOTED_META_KEYS
from ._actuator import Actuator
from ._debug_log import _write_zwingli_debug_event
from ._template import _find_pattern_match


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


def _suggest_verb(
    verb: str, verbs: Mapping[str, TranscriptVerbConfig]
) -> list[str]:
    """Return up to 3 canonical verb names likely intended by `verb`.

    Used when verb resolution falls through (unknown or disabled verb) so
    the dispatcher can attach a "did you mean?" hint to its metadata.
    Candidates are enabled verb names + their single-token aliases; matches
    against aliases map back to the canonical verb name. Multi-token
    aliases ("in python") are excluded because the unresolved input is
    always a single token. Uses difflib (Ratcliff-Obershelp) with cutoff
    0.6 — the stdlib default for typo suggestions.
    """
    import difflib

    needle = (verb or "").strip().lower()
    if not needle:
        return []
    candidate_to_canonical: dict[str, str] = {}
    for name, cfg in verbs.items():
        if not bool(cfg.enabled):
            continue
        candidate_to_canonical.setdefault(name.lower(), name)
        for alias in cfg.aliases or ():
            phrase = " ".join((alias or "").strip().lower().split())
            if not phrase or " " in phrase:
                continue
            candidate_to_canonical.setdefault(phrase, name)
    matches = difflib.get_close_matches(
        needle, list(candidate_to_canonical.keys()), n=3, cutoff=0.6
    )
    seen: set[str] = set()
    result: list[str] = []
    for match in matches:
        canonical = candidate_to_canonical[match]
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


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
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run a verb's action handler and build its top-level meta payload."""
    action = _resolve_action_from_verb_config(verb, verb_cfg)
    handler = _ACTIONS.get(action)
    if handler is None:
        raise RuntimeError(f"Unknown verb action: {action!r} (verb={verb!r})")

    cap = getattr(verb_cfg, "rate_limit_per_min", None)
    if isinstance(cap, int) and cap > 0:
        from voicepipe import rate_limit

        try:
            rate_limit.check_and_record(verb, cap)
        except rate_limit.RateLimitExceeded as exc:
            _write_zwingli_debug_event(
                {
                    "event": "rate_limited",
                    "verb": verb,
                    "cap_per_min": int(exc.cap_per_min),
                    "retry_after_seconds": float(exc.retry_after_seconds),
                }
            )
            raise

    out_text, inner_meta = handler(
        args,
        verb_cfg=verb_cfg,
        profiles=commands.llm_profiles,
        captures=captures,
        commands=commands,
        actuator=actuator,
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
    if isinstance(cap, int) and cap > 0:
        meta["rate_limit_per_min"] = cap
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
    actuator: Actuator | None = None,
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
            actuator=actuator,
        )

    verb_cfg = commands.verbs.get(verb) if verb else None

    if verb_cfg is not None and bool(verb_cfg.enabled):
        return _invoke_verb_handler(
            verb, verb_cfg, args, commands=commands, actuator=actuator
        )

    unknown_action = (commands.dispatch.unknown_verb or "").strip().lower() or "strip"
    handler = _ACTIONS.get(unknown_action)
    if handler is None:
        raise RuntimeError(f"Unknown dispatch.unknown_verb action: {unknown_action!r}")
    out_text, inner_meta = handler(
        raw_chunk,
        verb_cfg=None,
        profiles=commands.llm_profiles,
        captures=None,
        commands=commands,
        actuator=actuator,
    )
    meta: dict[str, Any] = {
        "mode": "unknown-verb",
        "verb": verb,
        "action": unknown_action,
    }
    if verb_cfg is not None and not bool(verb_cfg.enabled):
        meta["disabled_verb"] = verb
    suggestions = _suggest_verb(verb, commands.verbs)
    if suggestions:
        meta["did_you_mean"] = suggestions
    if inner_meta:
        meta["handler_meta"] = inner_meta
    return out_text, meta


# ---------- chain splitting ----------


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


def _dispatch_prompt(
    prompt: str,
    *,
    commands: TranscriptCommandsConfig,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    cleaned = (prompt or "").strip()
    chunks = _split_chain_chunks(cleaned, commands=commands)

    if len(chunks) == 1:
        verb, args = _resolve_verb_and_args(chunks[0], commands=commands)
        return _dispatch_single_step(
            verb, args, chunks[0], commands=commands, actuator=actuator
        )

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
            verb, step_input, step_chunk, commands=commands, actuator=actuator
        )
        prior_output = step_text

        if i < len(chunks) - 1:
            chain_metas.append(step_meta)
        else:
            final_text = step_text
            final_meta = step_meta

    final_meta["chain"] = chain_metas
    return final_text, final_meta
