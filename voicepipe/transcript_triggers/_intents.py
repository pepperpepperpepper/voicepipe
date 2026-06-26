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
    ACCESSIBILITY_GLOBAL_ACTIONS,
    CAP_ACCESSIBILITY_GLOBAL,
    CAP_CALENDAR,
    CAP_DIAL,
    CAP_EMAIL,
    CAP_MAP_SEARCH,
    CAP_NAVIGATE,
    CAP_OPEN_APP,
    CAP_OPEN_URL,
    CAP_REACH_CONTACT,
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


# Relative-offset markers for alarms phrased as a duration rather than a
# wall-clock time: "in 2 minutes", "2 minutes from now". We strip these
# markers and reuse the timer duration parser to get the offset seconds.
_ALARM_OFFSET_PREFIX_RE = re.compile(r"^in\s+", re.IGNORECASE)
_ALARM_OFFSET_SUFFIX_RE = re.compile(r"\s*\bfrom now\b\s*$", re.IGNORECASE)


def parse_alarm_offset_args(args: str) -> tuple[int, str | None] | None:
    """Parse an alarm spec expressed as a *relative* offset.

    Accepts ``2 minutes``, ``in 5 minutes``, ``2 minutes from now``,
    ``1h 30m wake up`` — i.e. a duration (optionally wrapped in
    ``in …`` / ``… from now``) rather than an absolute clock time.
    Returns ``(seconds_from_now, message)`` or ``None`` if the string is
    not a duration (e.g. ``7:30am``, which has no time unit and is left
    for :func:`parse_alarm_args`).
    """
    text = (args or "").strip()
    if not text:
        return None
    stripped = _ALARM_OFFSET_PREFIX_RE.sub("", text)
    stripped = _ALARM_OFFSET_SUFFIX_RE.sub("", stripped).strip()
    if not stripped:
        return None
    return parse_timer_args(stripped)


# ---------------------------------------------------------------------------
# Capability-unsupported messages (mirrors ⚠ zwingli style used elsewhere)
# ---------------------------------------------------------------------------


_UNSUPPORTED = {
    "search": "Web search is not supported on this device.",
    "open": "Opening URLs is not supported on this device.",
    "alarm": "Setting alarms is not supported on this device.",
    "timer": "Setting timers is not supported on this device.",
    "dial": "Dialing is not supported on this device.",
    "call": "Calling is not supported on this device.",
    "message": "Messaging contacts is not supported on this device.",
    "open_app": "Launching apps is not supported on this device.",
    "navigate": "Navigation is not supported on this device.",
    "places": "Map search is not supported on this device.",
    "accessibility_global": "System navigation is not supported on this device.",
    "calendar": "Creating calendar events is not supported on this device.",
    "email": "Composing email is not supported on this device.",
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
    # A relative offset ("in 2 minutes", "2 minutes from now") sets an alarm
    # at now+offset; the wall-clock time is resolved on-device (which knows
    # the local timezone). An absolute clock time ("7:30am") takes the
    # hour/minutes path. Try the offset first since "7:30" has no time unit
    # and won't match the duration parser.
    offset = parse_alarm_offset_args(prompt or "")
    if offset is not None:
        seconds, message = offset
        act = resolve_actuator(actuator)
        if CAP_SET_ALARM not in act.capabilities():
            return _unsupported("alarm")
        if not act.set_alarm(None, None, message, in_seconds=seconds):
            return _unsupported("alarm")
        meta: dict[str, Any] = {
            "ok": True,
            "intent": "set_alarm",
            "in_seconds": seconds,
        }
        if message:
            meta["message"] = message
        return "", meta

    parsed = parse_alarm_args(prompt or "")
    if parsed is None:
        return _bad_args(
            "alarm", "expected e.g. '7am wake up', '07:30', or 'in 5 minutes'"
        )
    hour, minutes, message = parsed
    act = resolve_actuator(actuator)
    if CAP_SET_ALARM not in act.capabilities():
        return _unsupported("alarm")
    if not act.set_alarm(hour, minutes, message):
        return _unsupported("alarm")
    meta = {
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


def _action_calendar(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Create a calendar event. v1 is title-only: the client opens its
    calendar app pre-filled with the title and the user picks the time
    (date/time parsing belongs on-device, which knows the timezone)."""
    del verb_cfg, profiles, captures, commands
    title = (prompt or "").strip()
    if not title:
        return _bad_args("calendar", "empty event title")
    act = resolve_actuator(actuator)
    if CAP_CALENDAR not in act.capabilities():
        return _unsupported("calendar")
    if not act.set_calendar_event(title):
        return _unsupported("calendar")
    return "", {"ok": True, "intent": "calendar_event", "title": title}


def parse_email_args(args: str) -> tuple[str, str, str]:
    """Parse the router's ``to=…; subject=…; body=…`` email args. Any field may
    be absent; if no ``key=value`` pairs are present, the whole string is the
    body. Returns ``(to, subject, body)``."""
    to = subject = body = ""
    found = False
    for part in (args or "").split(";"):
        key, sep, val = part.partition("=")
        if not sep:
            continue
        k, v = key.strip().lower(), val.strip()
        if k == "to":
            to, found = v, True
        elif k == "subject":
            subject, found = v, True
        elif k == "body":
            body, found = v, True
    if not found:
        body = (args or "").strip()
    return to, subject, body


def _action_email(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Compose an email: open the client's mail composer pre-filled with
    to/subject/body. The user picks the From account and confirms the
    recipient (the mail app autocompletes contacts), then sends."""
    del verb_cfg, profiles, captures, commands
    to, subject, body = parse_email_args(prompt or "")
    if not (to or subject or body):
        return _bad_args("email", "expected e.g. 'to=bob; subject=lunch; body=…'")
    act = resolve_actuator(actuator)
    if CAP_EMAIL not in act.capabilities():
        return _unsupported("email")
    if not act.compose_email(to, subject, body):
        return _unsupported("email")
    meta: dict[str, Any] = {"ok": True, "intent": "email"}
    if to:
        meta["to"] = to
    if subject:
        meta["subject"] = subject
    if body:
        meta["body"] = body
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


# Spoken / written platform aliases → canonical {whatsapp, signal, sms, phone}.
_VIA_ALIASES: dict[str, str] = {
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
    "whatsap": "whatsapp",
    "wa": "whatsapp",
    "signal": "signal",
    "sms": "sms",
    "text": "sms",
    "text message": "sms",
    "message": "sms",
    "phone": "phone",
    "cell": "phone",
    "cellphone": "phone",
    "mobile": "phone",
    "regular": "phone",
}

_TRUTHY = {"true", "1", "yes", "y", "video"}


def parse_reach_args(args: str) -> tuple[str, str, str, bool]:
    """Parse the router's ``name=…; via=…; body=…; video=…`` reach args.

    Returns ``(name, via, body, video)``. ``via`` is normalized through
    :data:`_VIA_ALIASES` (empty string if absent/unknown). When the string
    carries no ``key=value`` pairs at all, the whole thing is taken as the
    ``name`` (so a bare "Sam Spears" still works for a phone call).
    """
    name = via = body = ""
    video = False
    found = False
    for part in (args or "").split(";"):
        key, sep, val = part.partition("=")
        if not sep:
            continue
        k, v = key.strip().lower(), val.strip()
        if k == "name":
            name, found = v, True
        elif k in ("via", "platform", "on", "app"):
            via, found = _VIA_ALIASES.get(v.lower(), v.lower()), True
        elif k == "body":
            body, found = v, True
        elif k == "video":
            video, found = v.lower() in _TRUTHY, True
    if not found:
        name = (args or "").strip()
    return name, via, body, video


def _action_call(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Call someone. Two paths:

    * Phone — a business/place/person looked up by NAME ("the Sukhothai
      Hotel in Shanghai", "Sam Spears"). The client checks contacts first,
      else the server resolves the number (Serper); the dialer opens.
    * App — a saved contact via WhatsApp or Signal ("call Mom on
      WhatsApp"): ``via=whatsapp|signal`` (optionally ``video=true``)
      resolves the contact on-device and starts the call in that app.
    """
    del verb_cfg, profiles, captures, commands
    raw = (prompt or "").strip()
    if not raw:
        return _bad_args("call", "expected a name (optionally 'via=whatsapp|signal')")
    name, via, _body, video = parse_reach_args(raw)
    if via in ("whatsapp", "signal"):
        target = (name or raw).strip()
        if not target:
            return _bad_args("call", "expected a contact name")
        mode = "video" if video else "call"
        act = resolve_actuator(actuator)
        if CAP_REACH_CONTACT not in act.capabilities():
            return _unsupported("call")
        if not act.reach_contact(target, via, mode):
            return _unsupported("call")
        return "", {
            "ok": True,
            "intent": "reach_contact",
            "platform": via,
            "mode": mode,
            "name": target,
        }
    # Phone path: a bare name (or via=phone). Use the raw query unless the
    # router gave a structured name= field.
    query = name if name else raw
    act = resolve_actuator(actuator)
    if CAP_DIAL not in act.capabilities():
        return _unsupported("call")
    if not act.call_business(query):
        return (
            "⚠ zwingli: couldn't find a phone number for that",
            {"ok": False, "error": "lookup_failed", "intent": "call", "query": query},
        )
    return "", {"ok": True, "intent": "call", "query": query}


def _action_message(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Text/chat a saved contact: SMS by default, or WhatsApp / Signal via
    ``via=…``. The contact is resolved on-device; the messaging app opens
    pre-filled with the body and the user taps send."""
    del verb_cfg, profiles, captures, commands
    raw = (prompt or "").strip()
    if not raw:
        return _bad_args("message", "expected 'name=…; via=sms|whatsapp|signal; body=…'")
    name, via, body, _video = parse_reach_args(raw)
    if not via or via == "phone":
        via = "sms"
    if via not in ("sms", "whatsapp", "signal"):
        via = "sms"
    if not name:
        return _bad_args("message", "expected a contact name")
    act = resolve_actuator(actuator)
    if CAP_REACH_CONTACT not in act.capabilities():
        return _unsupported("message")
    if not act.reach_contact(name, via, "message", body=body or None):
        return _unsupported("message")
    meta: dict[str, Any] = {
        "ok": True,
        "intent": "reach_contact",
        "platform": via,
        "mode": "message",
        "name": name,
    }
    if body:
        meta["body"] = body
    return "", meta


# Spoken / written app names → canonical app token. The client maps the
# canonical token to an Android package. Unlisted names pass through
# lower-cased (the client still tries to resolve them).
_APP_ALIASES: dict[str, str] = {
    "whatsapp": "whatsapp",
    "whats app": "whatsapp",
    "whatsap": "whatsapp",
    "wa": "whatsapp",
    "wechat": "wechat",
    "we chat": "wechat",
    "weixin": "wechat",
    "signal": "signal",
    "telegram": "telegram",
    "instagram": "instagram",
    "insta": "instagram",
    "ig": "instagram",
    "messenger": "messenger",
    "facebook messenger": "messenger",
    "facebook": "facebook",
    "fb": "facebook",
    "twitter": "twitter",
    "x": "twitter",
    "snapchat": "snapchat",
    "snap": "snapchat",
    "discord": "discord",
    "slack": "slack",
    "viber": "viber",
    "line": "line",
}

# A leading "for"/"about" between the app and the search query is stripped so
# "search whatsapp for bob" → query "bob" (not "for bob").
_APP_QUERY_PREFIX_RE = re.compile(r"^(?:for|about)\s+", re.IGNORECASE)


def _normalize_app(name: str) -> str:
    key = " ".join((name or "").strip().lower().split())
    return _APP_ALIASES.get(key, key)


def parse_app_args(args: str) -> tuple[str, str | None]:
    """Parse the router's ``open_app`` args into ``(app, query_or_None)``.

    Accepts a structured ``app=…; query=…`` form, or a bare form where the
    FIRST recognized app alias is the app and the rest is the search query:
    ``"whatsapp"`` → ``("whatsapp", None)``; ``"whatsapp for bob"`` →
    ``("whatsapp", "bob")``; ``"wechat bob smith"`` → ``("wechat", "bob
    smith")``. The app token is normalized through :data:`_APP_ALIASES`.
    """
    text = (args or "").strip()
    if not text:
        return "", None
    # Structured form.
    if "=" in text and ("app=" in text.lower() or "query=" in text.lower()):
        app = query = ""
        for part in text.split(";"):
            key, sep, val = part.partition("=")
            if not sep:
                continue
            k, v = key.strip().lower(), val.strip()
            if k == "app":
                app = v
            elif k in ("query", "for", "contact", "name"):
                query = v
        return _normalize_app(app), (query or None)
    # Bare form: greedily match the longest leading app alias (handles
    # multi-word names like "we chat" / "facebook messenger").
    tokens = text.split()
    for take in range(min(2, len(tokens)), 0, -1):
        candidate = " ".join(tokens[:take]).lower()
        if candidate in _APP_ALIASES:
            rest = " ".join(tokens[take:]).strip()
            rest = _APP_QUERY_PREFIX_RE.sub("", rest).strip()
            return _APP_ALIASES[candidate], (rest or None)
    # No known alias: treat the first token as the app, remainder as query.
    rest = " ".join(tokens[1:]).strip()
    rest = _APP_QUERY_PREFIX_RE.sub("", rest).strip()
    return _normalize_app(tokens[0]), (rest or None)


def _action_open_app(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Launch a named app ("open WhatsApp"). With a trailing contact/query
    ("search WeChat for Bob"), the client copies the query to the clipboard
    so the user can paste it into the app's own search — these apps expose no
    external search deep link."""
    del verb_cfg, profiles, captures, commands
    app, query = parse_app_args(prompt or "")
    if not app:
        return _bad_args("open_app", "expected an app name, e.g. 'whatsapp'")
    act = resolve_actuator(actuator)
    if CAP_OPEN_APP not in act.capabilities():
        return _unsupported("open_app")
    if not act.open_app(app, query):
        return _unsupported("open_app")
    meta: dict[str, Any] = {"ok": True, "intent": "open_app", "app": app}
    if query:
        meta["query"] = query
    return "", meta


# Spoken travel-mode tokens → canonical mode strings. The canonical values
# match the Maps URI scheme on the executor side ("driving" → mode=d, etc.)
# but we keep them spelled out here so the metadata is readable in logs
# and tests don't depend on URI-encoding details.
_NAVIGATE_MODE_ALIASES: dict[str, str] = {
    "driving": "driving",
    "drive": "driving",
    "car": "driving",
    "walking": "walking",
    "walk": "walking",
    "biking": "bicycling",
    "bicycling": "bicycling",
    "cycling": "bicycling",
    "bike": "bicycling",
    "transit": "transit",
    "bus": "transit",
    "train": "transit",
    "subway": "transit",
    "metro": "transit",
}


def parse_navigate_args(args: str) -> tuple[str, str | None] | None:
    """Parse a spoken navigate spec into ``(destination, mode_or_None)``.

    Recognized shapes (case-insensitive):

    * ``paris`` → ``("paris", None)``
    * ``to paris`` → ``("paris", None)`` — strips a leading ``to``
    * ``driving paris`` → ``("paris", "driving")``
    * ``walking to the library`` → ``("the library", "walking")``
    * ``bike to alameda`` → ``("alameda", "bicycling")``
    * ``the library walking`` → ``("the library", "walking")`` — a mode word
      at the END is also accepted (the router sometimes appends it).

    Returns ``None`` for empty input or when the mode word consumes the
    whole tail (no destination left).
    """
    text = (args or "").strip()
    if not text:
        return None
    tokens = text.split()
    mode: str | None = None
    # Mode word may lead ("walking to X") or trail ("X walking"). Check the
    # front first, then the back, so either router phrasing resolves.
    if tokens and tokens[0].lower() in _NAVIGATE_MODE_ALIASES:
        mode = _NAVIGATE_MODE_ALIASES[tokens[0].lower()]
        tokens = tokens[1:]
    elif len(tokens) > 1 and tokens[-1].lower() in _NAVIGATE_MODE_ALIASES:
        mode = _NAVIGATE_MODE_ALIASES[tokens[-1].lower()]
        tokens = tokens[:-1]
    # Drop a leading "to" once — works for "navigate to X" and
    # "navigate driving to X" alike.
    if tokens and tokens[0].lower() == "to":
        tokens = tokens[1:]
    destination = " ".join(tokens).strip()
    if not destination:
        return None
    return destination, mode


def _action_navigate(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    del verb_cfg, profiles, captures, commands
    parsed = parse_navigate_args(prompt or "")
    if parsed is None:
        return _bad_args("navigate", "expected e.g. 'paris' or 'walking to library'")
    destination, mode = parsed
    act = resolve_actuator(actuator)
    if CAP_NAVIGATE not in act.capabilities():
        return _unsupported("navigate")
    if not act.navigate(destination, mode):
        return _unsupported("navigate")
    meta: dict[str, Any] = {
        "ok": True,
        "intent": "navigate",
        "destination": destination,
    }
    if mode:
        meta["mode"] = mode
    return "", meta


# Spoken filler stripped from a places query so "show me nearby gas stations"
# → "gas stations". "near me" / "nearby" / "around here" are dropped because
# the map already searches around the current location.
_PLACES_STRIP_RE = re.compile(
    r"\b(?:show me|find me|find|where(?:'s| is)|the nearest|nearest|"
    r"nearby|near me|around here|close by|closest)\b",
    re.IGNORECASE,
)


def parse_places_args(args: str) -> str:
    """Reduce a spoken places request to the bare query: drop filler like
    "show me", "nearby", "near me", "nearest". "show me nearby gas stations"
    → "gas stations"; "where's the nearest pharmacy" → "pharmacy"."""
    text = _PLACES_STRIP_RE.sub(" ", args or "")
    # Collapse whitespace and strip leading "for"/"a"/"an"/"to" left behind.
    text = " ".join(text.split())
    text = re.sub(r"^(?:for|a|an|to)\s+", "", text, flags=re.IGNORECASE)
    return text.strip()


def _action_places(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """Open a map showing a places search ("nearby gas stations", "nearest
    pharmacy") — results around the current location, not turn-by-turn routing
    (that's `navigate`)."""
    del verb_cfg, profiles, captures, commands
    query = parse_places_args(prompt or "")
    if not query:
        return _bad_args("places", "expected a place type, e.g. 'gas stations'")
    act = resolve_actuator(actuator)
    if CAP_MAP_SEARCH not in act.capabilities():
        return _unsupported("places")
    if not act.find_places(query):
        return _unsupported("places")
    return "", {"ok": True, "intent": "map_search", "query": query}


# ---------------------------------------------------------------------------
# Accessibility-global verbs (back / home / recents / notifications /
# quick_settings) — one verb per action, all sharing the same handler.
# These take no args; any tail tokens are ignored so STT chatter doesn't
# turn `zwingli back` into a bad-args path.
# ---------------------------------------------------------------------------


def _make_accessibility_global_handler(action: str):
    """Build a handler that queues a single ``accessibility_global`` action.

    The action name is closed over so each verb (back/home/recents/etc.)
    becomes a distinct entry in ``_ACTIONS`` without duplicating the
    capability-check + actuator-call dance.
    """
    if action not in ACCESSIBILITY_GLOBAL_ACTIONS:
        raise ValueError(f"unsupported accessibility-global action: {action!r}")

    def _handler(
        prompt: str,
        *,
        verb_cfg: TranscriptVerbConfig | None = None,
        profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
        captures: Mapping[str, str] | None = None,
        commands: TranscriptCommandsConfig | None = None,
        actuator: Actuator | None = None,
    ) -> tuple[str, dict[str, Any]]:
        del prompt, verb_cfg, profiles, captures, commands
        act = resolve_actuator(actuator)
        if CAP_ACCESSIBILITY_GLOBAL not in act.capabilities():
            return _unsupported("accessibility_global")
        if not act.accessibility_global(action):
            return _unsupported("accessibility_global")
        return "", {
            "ok": True,
            "intent": "accessibility_global",
            "action": action,
        }

    _handler.__name__ = f"_action_{action}"
    _handler.__qualname__ = _handler.__name__
    return _handler


_action_back = _make_accessibility_global_handler("back")
_action_home = _make_accessibility_global_handler("home")
_action_recents = _make_accessibility_global_handler("recents")
_action_notifications = _make_accessibility_global_handler("notifications")
_action_quick_settings = _make_accessibility_global_handler("quick_settings")
