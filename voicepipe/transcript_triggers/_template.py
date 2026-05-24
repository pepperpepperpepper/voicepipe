"""Shared template/pattern primitives used by actions and dispatch.

- ``_render_user_prompt_template``: ``{{text}}`` and ``{{capture_name}}``
  substitution for LLM user prompts. If ``{{text}}`` isn't referenced
  in the template, the text is appended after the rendered output (so
  prompts that only set context still see the input).
- ``_compile_verb_pattern``: turn a ``"open {target} in vim"``-style
  pattern into an anchored case-insensitive regex with named captures.
- ``_find_pattern_match``: walk a TranscriptCommandsConfig and return
  ``(verb_name, captures)`` for the first enabled verb whose pattern
  matches an input chunk.
- ``_substitute_command_template``: ``{name}`` → captures substitution
  for shell/execute ``command_template`` fields.
"""

from __future__ import annotations

import re
from typing import Mapping

from voicepipe.config import TranscriptCommandsConfig


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
