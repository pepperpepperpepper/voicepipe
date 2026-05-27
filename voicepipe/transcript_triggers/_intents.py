"""Intent-style action handlers: search / open / alarm / timer / dial.

These verbs map a transcript tail to a structured call on the
:class:`~voicepipe.transcript_triggers._actuator.Actuator`. On a desktop
that means opening a browser, etc. Through
:class:`~voicepipe.dispatch_server.ServerActuator` it means queueing a
``client_actions`` entry so a Zwangli phone executes the corresponding
Android Intent (``ACTION_WEB_SEARCH``, ``AlarmClock.ACTION_SET_ALARM``,
…).

Each handler returns an empty ``output_text`` on success — the action is
the side effect (browser opens, alarm pre-fills); we don't want the
emission layer typing "weather tokyo" into a focused field. On graceful
skip (actuator can't do it) we surface a ``⚠ zwingli`` notice via the
standard error path.

The arg parsers (:func:`parse_alarm_args`, :func:`parse_timer_args`) are
module-level utilities so tests can exercise them directly without
spinning the dispatcher.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._actuator import (
    CAP_DIAL,
    CAP_OPEN_URL,
    CAP_SET_ALARM,
    CAP_SET_TIMER,
    CAP_WEB_SEARCH,
    Actuator,
    resolve_actuator,
)


# ---------------------------------------------------------------------------
# Arg parsers (exposed for unit testing)
# ---------------------------------------------------------------------------


# `7`, `7:30`, `07:30`, `7.30`, optional am/pm, optional trailing message.
# Tolerates `a.m.` / `p.m.` punctuation.
_ALARM_RE = re.compile(
    r"""
    ^\s*
    (?P<hour>\d{1,2})
    (?:[:.\s](?P<minutes>\d{2}))?
    \s*
    (?P<meridiem>a\.?m\.?|p\.?m\.?)?
    (?:\s+(?P<message>\S.*?))?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)


def parse_alarm_args(args: str) -> tuple[int, int, str | None] | None:
    """Parse a spoken alarm spec.

    Accepts: ``7am``, ``7 AM``, ``7:30pm``, ``7:30 p.m. wake up``,
    ``19:30``, ``07:00 standup``. Returns ``(hour, minutes, message)``
    with ``hour ∈ 0..23`` and ``minutes ∈ 0..59``, or ``None`` if the
    string cannot be parsed unambiguously.
    """
    text = (args or "").strip()
    if not text:
        return None
    m = _ALARM_RE.match(text)
    if not m:
        return None
    hour = int(m["hour"])
    minutes = int(m["minutes"] or 0)
    meridiem = (m["meridiem"] or "").lower().replace(".", "")
    if meridiem in ("am", "pm"):
        if hour < 1 or hour > 12:
            return None
        if hour == 12:
            hour = 0
        if meridiem == "pm":
            hour += 12
    else:
        if hour > 23:
            return None
    if minutes > 59:
        return None
    message = (m["message"] or "").strip() or None
    return hour, minutes, message


# Order: longest units first so `min` doesn't get partially eaten by `m\b`.
_DURATION_TOKEN_RE = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
    re.IGNORECASE,
)


def parse_timer_args(args: str) -> tuple[int, str | None] | None:
    """Parse a spoken timer duration.

    Accepts compositions like ``5 minutes``, ``5m``, ``1h 30m``,
    ``90 seconds``, ``2 hours 15 minutes pasta``. Numbers without a
    unit are rejected as ambiguous. Returns ``(total_seconds, message)``
    with ``1 ≤ total_seconds ≤ 86400`` or ``None``.
    """
    text = (args or "").strip()
    if not text:
        return None
    total = 0
    matched_any = False
    pos = 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        m = _DURATION_TOKEN_RE.match(text, pos)
        if not m:
            break
        unit = m["unit"].lower()
        n = int(m["n"])
        if unit in ("h", "hr", "hrs", "hour", "hours"):
            total += n * 3600
        elif unit in ("m", "min", "mins", "minute", "minutes"):
            total += n * 60
        else:
            total += n
        matched_any = True
        pos = m.end()
    if not matched_any or total <= 0 or total > 86_400:
        return None
    message = text[pos:].strip() or None
    return total, message


# ---------------------------------------------------------------------------
# Capability-unsupported messages (mirrors ⚠ zwingli style used elsewhere)
# ---------------------------------------------------------------------------


_UNSUPPORTED = {
    "search": "Web search is not supported on this device.",
    "open": "Opening URLs is not supported on this device.",
    "alarm": "Setting alarms is not supported on this device.",
    "timer": "Setting timers is not supported on this device.",
    "dial": "Dialing is not supported on this device.",
}


def _unsupported(kind: str) -> tuple[str, dict[str, Any]]:
    return (
        f"⚠ zwingli: {_UNSUPPORTED[kind]}",
        {"ok": False, "error": "capability_unsupported", "intent": kind},
    )


def _bad_args(kind: str, hint: str) -> tuple[str, dict[str, Any]]:
    return (
        f"⚠ zwingli: couldn't parse {kind} args ({hint})",
        {"ok": False, "error": "bad_args", "intent": kind, "hint": hint},
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _action_search(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    query = (prompt or "").strip()
    if not query:
        return _bad_args("search", "empty query")
    act = resolve_actuator(actuator)
    if CAP_WEB_SEARCH not in act.capabilities():
        return _unsupported("search")
    if not act.web_search(query):
        return _unsupported("search")
    return "", {"ok": True, "intent": "web_search", "query": query}


# Matches a leading bare scheme word like ``http example.com`` or
# ``HTTPS example.com``. STT usually emits the scheme as a literal word
# rather than as ``https://``, so without this normalization those
# transcripts get the scheme prepended twice.
_OPEN_SCHEME_WORD_RE = re.compile(r"^(https?)\s+(\S.*)$", re.IGNORECASE)


def _normalize_open_url(raw: str) -> str:
    """Coerce a spoken ``open …`` argument into a parseable URL.

    Three shapes are handled:

    * ``https://example.com`` — already a URL, returned as-is.
    * ``http example.com`` / ``HTTPS example.com`` — a spoken scheme
      followed by the host; the scheme word is replaced with
      ``scheme://``.
    * ``example.com`` — a bare host with no scheme word; ``https://``
      is prepended.
    """
    raw = raw.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", raw):
        return raw
    m = _OPEN_SCHEME_WORD_RE.match(raw)
    if m:
        return f"{m.group(1).lower()}://{m.group(2).strip()}"
    return "https://" + raw


def _action_open(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    raw = (prompt or "").strip()
    if not raw:
        return _bad_args("open", "empty url")
    url = _normalize_open_url(raw)
    act = resolve_actuator(actuator)
    if CAP_OPEN_URL not in act.capabilities():
        return _unsupported("open")
    if not act.open_url(url):
        return _unsupported("open")
    return "", {"ok": True, "intent": "open_url", "url": url}


def _action_alarm(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    parsed = parse_alarm_args(prompt or "")
    if parsed is None:
        return _bad_args("alarm", "expected e.g. '7am wake up' or '07:30'")
    hour, minutes, message = parsed
    act = resolve_actuator(actuator)
    if CAP_SET_ALARM not in act.capabilities():
        return _unsupported("alarm")
    if not act.set_alarm(hour, minutes, message):
        return _unsupported("alarm")
    meta: dict[str, Any] = {
        "ok": True,
        "intent": "set_alarm",
        "hour": hour,
        "minutes": minutes,
    }
    if message:
        meta["message"] = message
    return "", meta


def _action_timer(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    parsed = parse_timer_args(prompt or "")
    if parsed is None:
        return _bad_args("timer", "expected e.g. '5 minutes' or '1h 30m'")
    seconds, message = parsed
    act = resolve_actuator(actuator)
    if CAP_SET_TIMER not in act.capabilities():
        return _unsupported("timer")
    if not act.set_timer(seconds, message):
        return _unsupported("timer")
    meta: dict[str, Any] = {
        "ok": True,
        "intent": "set_timer",
        "seconds": seconds,
    }
    if message:
        meta["message"] = message
    return "", meta


_DIAL_KEEP_RE = re.compile(r"[+0-9*#,;]")

# STT typically transcribes dial-pad symbols as words (``plus``, ``star``,
# ``pound``…) rather than the literal characters. Without this map the
# subsequent keep-filter strips them entirely, so a spoken
# ``plus 1 800 …`` would silently drop the international ``+``. Word
# boundaries keep matches from chewing into adjacent tokens.
_DIAL_SPOKEN_SYMBOLS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bplus\b", re.IGNORECASE), "+"),
    (re.compile(r"\b(?:pound|hash)\b", re.IGNORECASE), "#"),
    (re.compile(r"\b(?:star|asterisk)\b", re.IGNORECASE), "*"),
)


def _action_dial(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    raw = (prompt or "").strip()
    if not raw:
        return _bad_args("dial", "empty number")
    for pattern, replacement in _DIAL_SPOKEN_SYMBOLS:
        raw = pattern.sub(replacement, raw)
    # Keep digits, +, *, #, and pause separators; strip spaces, dashes,
    # parentheses, etc., that STT loves to insert. tel: URIs don't want
    # them anyway.
    number = "".join(_DIAL_KEEP_RE.findall(raw))
    if not number:
        return _bad_args("dial", "no digits found")
    act = resolve_actuator(actuator)
    if CAP_DIAL not in act.capabilities():
        return _unsupported("dial")
    if not act.dial(number):
        return _unsupported("dial")
    return "", {"ok": True, "intent": "dial", "number": number}
