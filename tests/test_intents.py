"""Tests for the intent-style action handlers (search/open/alarm/timer/dial).

Two layers:
  * arg-parser unit tests — exercise :func:`parse_alarm_args` and
    :func:`parse_timer_args` directly so spoken-format edge cases live
    in one place.
  * dispatcher integration — drive the verbs through
    :func:`voicepipe.transcript_triggers.apply_transcript_triggers` and
    assert the actuator received the right call and the output_text /
    metadata are shaped for the emission layer (empty output for
    success; ``⚠ zwingli`` notice on failure).
"""
from __future__ import annotations

import pytest

import voicepipe.config as config
import voicepipe.transcript_triggers as tt
from voicepipe.transcript_triggers._actuator import (
    CAP_DIAL,
    CAP_OPEN_URL,
    CAP_SET_ALARM,
    CAP_SET_TIMER,
    CAP_WEB_SEARCH,
    InMemoryActuator,
)
from voicepipe.transcript_triggers._intents import (
    _normalize_open_url,
    parse_alarm_args,
    parse_timer_args,
)


# ---------------------------------------------------------------------------
# parse_alarm_args
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("7am", (7, 0, None)),
        ("7 am", (7, 0, None)),
        ("7 AM", (7, 0, None)),
        ("7pm", (19, 0, None)),
        ("7:30am", (7, 30, None)),
        ("7:30 PM", (19, 30, None)),
        ("7:30 pm wake up", (19, 30, "wake up")),
        ("12am", (0, 0, None)),  # midnight
        ("12pm", (12, 0, None)),  # noon
        ("12:30am", (0, 30, None)),
        ("12:30pm", (12, 30, None)),
        ("19:30", (19, 30, None)),
        ("07:30", (7, 30, None)),
        ("19:30 standup", (19, 30, "standup")),
        ("0", (0, 0, None)),
        ("23:59", (23, 59, None)),
        ("7.30am", (7, 30, None)),  # dot separator
        ("7 30 am", (7, 30, None)),  # space separator
        ("7 a.m. dawn run", (7, 0, "dawn run")),
        ("7 p.m. dinner", (19, 0, "dinner")),
    ],
)
def test_parse_alarm_args_accepts(text: str, expected: tuple[int, int, str | None]) -> None:
    assert parse_alarm_args(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "tomorrow",
        "25:00",       # invalid 24-hour
        "13am",        # am/pm requires 1..12
        "0am",         # am/pm requires 1..12
        "7:60am",      # minutes out of range
        "24:00",       # 24-hour upper bound
        "abc",
    ],
)
def test_parse_alarm_args_rejects(text: str) -> None:
    assert parse_alarm_args(text) is None


# ---------------------------------------------------------------------------
# parse_timer_args
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5 minutes", (300, None)),
        ("5 minute", (300, None)),
        ("5 mins", (300, None)),
        ("5 min", (300, None)),
        ("5m", (300, None)),
        ("30 seconds", (30, None)),
        ("30 second", (30, None)),
        ("30s", (30, None)),
        ("1 hour", (3600, None)),
        ("1h", (3600, None)),
        ("1 hr", (3600, None)),
        ("1h 30m", (5400, None)),
        ("1 hour 30 minutes", (5400, None)),
        ("2 hours 15 minutes pasta", (8100, "pasta")),
        ("90 seconds", (90, None)),
        ("5 minutes pasta", (300, "pasta")),
        ("5m laundry", (300, "laundry")),
        ("1h 30m 15s big bake", (5415, "big bake")),
        ("24h", (86_400, None)),  # boundary
    ],
)
def test_parse_timer_args_accepts(text: str, expected: tuple[int, str | None]) -> None:
    assert parse_timer_args(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "90",            # no unit → ambiguous → reject
        "pasta",
        "0 minutes",     # total must be > 0
        "0s",
        "25 hours",      # exceeds 86400 boundary
    ],
)
def test_parse_timer_args_rejects(text: str) -> None:
    assert parse_timer_args(text) is None


# ---------------------------------------------------------------------------
# Dispatcher integration — happy path
# ---------------------------------------------------------------------------


def _commands_for(verb: str, action: str) -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            verb: config.TranscriptVerbConfig(
                action=action, enabled=True, type="builtin"
            ),
        },
    )


def test_search_verb_routes_through_actuator() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli search weather tokyo",
        commands=_commands_for("search", "search"),
        actuator=act,
    )
    assert out == ""
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["ok"] is True
    assert handler_meta["intent"] == "web_search"
    assert handler_meta["query"] == "weather tokyo"
    assert act.web_search_calls == ["weather tokyo"]


def test_open_verb_routes_through_actuator() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli open https://example.com/",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.open_url_calls == ["https://example.com/"]


def test_open_verb_adds_https_for_bare_host() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli open example.com",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.open_url_calls == ["https://example.com"]


def test_alarm_verb_routes_through_actuator_with_message() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm 7am wake up",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.set_alarm_calls == [
        {"hour": 7, "minutes": 0, "message": "wake up"}
    ]


def test_alarm_verb_without_message() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm 19:30",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.set_alarm_calls == [{"hour": 19, "minutes": 30, "message": None}]


def test_timer_verb_routes_through_actuator() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli timer 5 minutes pasta",
        commands=_commands_for("timer", "timer"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.set_timer_calls == [{"seconds": 300, "message": "pasta"}]


def test_dial_verb_strips_punctuation() -> None:
    """STT often emits dashes/parens/spaces in phone numbers — strip them
    before producing the tel: URI."""
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli dial +1 (555) 555-0100",
        commands=_commands_for("dial", "dial"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.dial_calls == ["+15555550100"]


@pytest.mark.parametrize(
    "transcript,expected",
    [
        # The original bug: spoken "plus" got dropped by the keep-filter,
        # turning an international call into a domestic one.
        ("dial plus 1 800 555 0100", "+18005550100"),
        ("dial Plus 44 20 7946 0958", "+442079460958"),
        # Star / pound for in-call codes and pre-dial features.
        ("dial star 67 5550100", "*675550100"),
        ("dial asterisk 67 5550100", "*675550100"),
        ("dial pound 1234", "#1234"),
        ("dial hash 1234", "#1234"),
        # Spoken symbol words are case-insensitive.
        ("dial PLUS 1 555 0100", "+15550100"),
        # Mixed: a literal "+" alongside a spoken "star" should keep both.
        ("dial +1 star 5550100", "+1*5550100"),
    ],
)
def test_dial_verb_converts_spoken_symbols(transcript: str, expected: str) -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        f"zwingli {transcript}",
        commands=_commands_for("dial", "dial"),
        actuator=act,
    )
    assert out == "", out
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.dial_calls == [expected]


# ---------------------------------------------------------------------------
# _normalize_open_url (unit tests for the spoken-scheme fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Already a URL — passed through untouched.
        ("https://example.com", "https://example.com"),
        ("http://example.com/path?q=1", "http://example.com/path?q=1"),
        ("ftp://files.example.com/", "ftp://files.example.com/"),
        # Bare host — https:// is prepended.
        ("example.com", "https://example.com"),
        ("sub.example.com/x", "https://sub.example.com/x"),
        # Spoken scheme prefix — the scheme word is replaced with scheme://,
        # not duplicated. This was the bug: "https example.com" used to
        # become "https://https example.com".
        ("https example.com", "https://example.com"),
        ("http example.com", "http://example.com"),
        ("HTTPS example.com", "https://example.com"),
        ("Http example.com/path", "http://example.com/path"),
        # Extra whitespace around the scheme word is fine.
        ("  https   example.com  ", "https://example.com"),
    ],
)
def test_normalize_open_url(raw: str, expected: str) -> None:
    assert _normalize_open_url(raw) == expected


def test_open_verb_handles_spoken_https_prefix() -> None:
    """End-to-end: 'open https example.com' fires open_url with the
    expected URL instead of the previously mangled 'https://https example.com'."""
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli open https example.com",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.open_url_calls == ["https://example.com"]


def test_open_verb_handles_spoken_http_prefix() -> None:
    """The spoken scheme is honored, not silently upgraded to https."""
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli open http example.com",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.open_url_calls == ["http://example.com"]


# ---------------------------------------------------------------------------
# Bad-args path: ⚠ zwingli notice, no actuator call
# ---------------------------------------------------------------------------


def test_alarm_verb_bad_args_returns_warning_without_calling_actuator() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm tomorrow morning",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.set_alarm_calls == []


def test_timer_verb_bad_args_returns_warning_without_calling_actuator() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli timer pasta",
        commands=_commands_for("timer", "timer"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.set_timer_calls == []


def test_search_verb_empty_query_returns_warning() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli search",
        commands=_commands_for("search", "search"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.web_search_calls == []


def test_dial_verb_no_digits_returns_warning() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli dial mom",
        commands=_commands_for("dial", "dial"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.dial_calls == []


# ---------------------------------------------------------------------------
# Capability-missing path: graceful skip with ⚠ notice
# ---------------------------------------------------------------------------


def test_alarm_graceful_skip_when_capability_missing() -> None:
    """A desktop (which doesn't advertise CAP_SET_ALARM) must produce a
    polite notice and not invoke the actuator's set_alarm at all."""
    bare = frozenset({CAP_OPEN_URL, CAP_WEB_SEARCH})  # no set_alarm
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm 7am",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert "not supported on this device" in out
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.set_alarm_calls == []


def test_timer_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_OPEN_URL})  # no set_timer
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli timer 5m",
        commands=_commands_for("timer", "timer"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert "not supported" in out
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.set_timer_calls == []


def test_dial_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_OPEN_URL})  # no dial
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli dial +15555550100",
        commands=_commands_for("dial", "dial"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.dial_calls == []


def test_search_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_DIAL})  # no web_search
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli search weather",
        commands=_commands_for("search", "search"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.web_search_calls == []


def test_open_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_DIAL})  # no open_url
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli open example.com",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.open_url_calls == []


# ---------------------------------------------------------------------------
# End-to-end: server-side queueing matches what Zwangli's parser expects
# ---------------------------------------------------------------------------


def _intent_actions(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Drop the ``feedback`` entries the audio-feedback hook auto-queues
    on every successful match; tests care only about the intent verb's
    own client_action."""
    return [a for a in actions if a.get("type") != "feedback"]


def test_alarm_verb_via_server_actuator_queues_zwangli_compatible_action() -> None:
    """The whole point: a transcript through the dispatcher with a
    :class:`ServerActuator` emits a client_actions[] entry shaped exactly
    like what Zwangli's ClientActions parser expects."""
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm 7:30am morning workout",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert _intent_actions(act.client_actions) == [
        {
            "type": "set_alarm",
            "hour": 7,
            "minutes": 30,
            "message": "morning workout",
        }
    ]


def test_search_verb_via_server_actuator_queues_web_search_action() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli search rain in spain",
        commands=_commands_for("search", "search"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "web_search", "query": "rain in spain"}
    ]


def test_timer_verb_via_server_actuator_queues_set_timer_action() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli timer 1h 30m big bake",
        commands=_commands_for("timer", "timer"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "set_timer", "seconds": 5400, "message": "big bake"}
    ]


def test_dial_verb_via_server_actuator_queues_dial_action() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli dial (555) 555-0100",
        commands=_commands_for("dial", "dial"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "dial", "number": "5555550100"}
    ]


def test_open_verb_via_server_actuator_queues_open_url_action() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli open https://duckduckgo.com",
        commands=_commands_for("open", "open"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "open_url", "url": "https://duckduckgo.com"}
    ]
