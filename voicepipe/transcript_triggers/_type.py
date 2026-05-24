"""``type`` action: parse a spoken phrase into a keypress sequence.

The tokenizer flattens punctuation/whitespace and drops a list of
"filler" words that voice users sprinkle in (``please``, ``and``,
``press``…). The parser recognises modifier words (``control``,
``shift``…), key aliases (``enter``, ``escape``, ``up arrow``…),
function keys (``f1``–``f24``), and chord syntax (``ctrl+b``).
Anything that doesn't match becomes literal text in the output.
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)


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
