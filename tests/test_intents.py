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
    _action_call,
    _action_message,
    _action_open_app,
    _normalize_open_url,
    parse_alarm_args,
    parse_alarm_offset_args,
    parse_app_args,
    parse_navigate_args,
    parse_reach_args,
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


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2 minutes", (120, None)),
        ("in 2 minutes", (120, None)),
        ("2 minutes from now", (120, None)),
        ("in 90 seconds", (90, None)),
        ("1h 30m bread", (5400, "bread")),
        ("in 5 minutes pasta", (300, "pasta")),
    ],
)
def test_parse_alarm_offset_args_accepts(
    text: str, expected: tuple[int, str | None]
) -> None:
    assert parse_alarm_offset_args(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "7am",        # absolute clock time — no duration unit
        "7:30",       # absolute clock time
        "19:30",      # absolute clock time
        "noon",
    ],
)
def test_parse_alarm_offset_args_rejects_absolute_times(text: str) -> None:
    # Absolute times have no duration unit and must fall through to
    # parse_alarm_args, not be treated as a relative offset.
    assert parse_alarm_offset_args(text) is None


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
        {"hour": 7, "minutes": 0, "message": "wake up", "in_seconds": None}
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
    assert act.set_alarm_calls == [
        {"hour": 19, "minutes": 30, "message": None, "in_seconds": None}
    ]


def test_alarm_verb_relative_offset_sets_in_seconds() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli alarm 2 minutes from now",
        commands=_commands_for("alarm", "alarm"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert meta["meta"]["handler_meta"]["in_seconds"] == 120
    assert act.set_alarm_calls == [
        {"hour": None, "minutes": None, "message": None, "in_seconds": 120}
    ]


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


def test_call_verb_routes_business_name_to_call_business() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli call Sukhothai Hotel Shanghai",
        commands=_commands_for("call", "call"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.call_business_calls == ["Sukhothai Hotel Shanghai"]


def test_call_verb_without_dial_capability_skips() -> None:
    from voicepipe.transcript_triggers._actuator import CAP_WEB_SEARCH

    act = InMemoryActuator(caps=frozenset({CAP_WEB_SEARCH}))  # no dial
    _out, meta = _action_call("Joe's Pizza", actuator=act)
    assert meta["error"] == "capability_unsupported"
    assert act.call_business_calls == []


# ---------------------------------------------------------------------------
# parse_reach_args / call-via-app / message
# ---------------------------------------------------------------------------


def test_parse_reach_args_structured_and_bare() -> None:
    assert parse_reach_args("name=Mom; via=whatsapp; video=true") == (
        "Mom",
        "whatsapp",
        "",
        True,
    )
    assert parse_reach_args("name=Sam; via=signal; body=call me") == (
        "Sam",
        "signal",
        "call me",
        False,
    )
    # Aliases normalize; bare strings become the name with no platform.
    assert parse_reach_args("name=Bob; via=text")[1] == "sms"
    assert parse_reach_args("Sukhothai Hotel") == ("Sukhothai Hotel", "", "", False)


def test_call_verb_via_whatsapp_routes_to_reach_contact() -> None:
    act = InMemoryActuator()
    out, meta = _action_call("name=Mom; via=whatsapp", actuator=act)
    assert out == ""
    assert meta["intent"] == "reach_contact"
    assert meta["platform"] == "whatsapp"
    assert meta["mode"] == "call"
    assert act.reach_contact_calls == [
        {"name": "Mom", "platform": "whatsapp", "mode": "call", "body": None}
    ]
    # Bare name still routes to the phone path (web/contact lookup).
    assert act.call_business_calls == []


def test_call_verb_via_signal_video() -> None:
    act = InMemoryActuator()
    _out, meta = _action_call("name=Dad; via=signal; video=true", actuator=act)
    assert meta["mode"] == "video"
    assert act.reach_contact_calls[0]["platform"] == "signal"


def test_message_verb_defaults_to_sms() -> None:
    act = InMemoryActuator()
    out, meta = _action_message("name=Sam; body=running late", actuator=act)
    assert out == ""
    assert meta["intent"] == "reach_contact"
    assert meta["platform"] == "sms"
    assert meta["mode"] == "message"
    assert act.reach_contact_calls == [
        {"name": "Sam", "platform": "sms", "mode": "message", "body": "running late"}
    ]


def test_message_verb_whatsapp() -> None:
    act = InMemoryActuator()
    _out, meta = _action_message("name=Mom; via=whatsapp; body=hi", actuator=act)
    assert meta["platform"] == "whatsapp"
    assert act.reach_contact_calls[0]["body"] == "hi"


def test_message_verb_without_reach_capability_skips() -> None:
    from voicepipe.transcript_triggers._actuator import CAP_WEB_SEARCH

    act = InMemoryActuator(caps=frozenset({CAP_WEB_SEARCH}))
    _out, meta = _action_message("name=Sam; body=hi", actuator=act)
    assert meta["error"] == "capability_unsupported"
    assert act.reach_contact_calls == []


# ---------------------------------------------------------------------------
# parse_app_args / open_app
# ---------------------------------------------------------------------------


def test_parse_app_args_bare_structured_and_aliases() -> None:
    assert parse_app_args("whatsapp") == ("whatsapp", None)
    assert parse_app_args("whatsapp for bob") == ("whatsapp", "bob")
    # Multi-word app name; "for" prefix on the query is stripped.
    assert parse_app_args("we chat for Bob Smith") == ("wechat", "Bob Smith")
    assert parse_app_args("app=wechat; query=Bob Smith") == ("wechat", "Bob Smith")
    # Aliases normalize.
    assert parse_app_args("ig") == ("instagram", None)
    assert parse_app_args("x") == ("twitter", None)


def test_open_app_verb_routes_to_actuator() -> None:
    act = InMemoryActuator()
    out, meta = _action_open_app("whatsapp for Bob", actuator=act)
    assert out == ""
    assert meta["intent"] == "open_app"
    assert meta["app"] == "whatsapp"
    assert meta["query"] == "Bob"
    assert act.open_app_calls == [{"app": "whatsapp", "query": "Bob"}]


def test_open_app_verb_without_capability_skips() -> None:
    from voicepipe.transcript_triggers._actuator import CAP_WEB_SEARCH

    act = InMemoryActuator(caps=frozenset({CAP_WEB_SEARCH}))
    _out, meta = _action_open_app("whatsapp", actuator=act)
    assert meta["error"] == "capability_unsupported"
    assert act.open_app_calls == []


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


# ---------------------------------------------------------------------------
# parse_navigate_args
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # Bare destination — no mode word.
        ("paris", ("paris", None)),
        ("the white house", ("the white house", None)),
        # Leading "to " gets stripped.
        ("to paris", ("paris", None)),
        ("TO Paris", ("Paris", None)),
        # Mode tokens at the start.
        ("driving paris", ("paris", "driving")),
        ("drive paris", ("paris", "driving")),
        ("car paris", ("paris", "driving")),
        ("walking the library", ("the library", "walking")),
        ("walk to the library", ("the library", "walking")),
        ("bike alameda", ("alameda", "bicycling")),
        ("biking to alameda", ("alameda", "bicycling")),
        ("cycling alameda", ("alameda", "bicycling")),
        ("bicycling alameda", ("alameda", "bicycling")),
        ("transit airport", ("airport", "transit")),
        ("bus airport", ("airport", "transit")),
        ("train downtown", ("downtown", "transit")),
        ("subway downtown", ("downtown", "transit")),
        ("metro to downtown", ("downtown", "transit")),
        # Mode + leading "to" together.
        ("driving to paris", ("paris", "driving")),
        # Mode word at the END (router sometimes appends it).
        ("the library walking", ("the library", "walking")),
        ("paris driving", ("paris", "driving")),
        ("downtown transit", ("downtown", "transit")),
        # A leading mode wins when both ends look like modes.
        ("walking the bike shop", ("the bike shop", "walking")),
        # Case-insensitive mode words.
        ("WALKING the park", ("the park", "walking")),
        # Extra whitespace.
        ("  driving   to   paris  ", ("paris", "driving")),
    ],
)
def test_parse_navigate_args_accepts(text: str, expected: tuple[str, str | None]) -> None:
    assert parse_navigate_args(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        # Mode word alone (no destination left after stripping it).
        "driving",
        "walk",
        "transit",
        # Mode word + bare "to" with nothing after.
        "driving to",
        "walking to ",
    ],
)
def test_parse_navigate_args_rejects(text: str) -> None:
    assert parse_navigate_args(text) is None


# ---------------------------------------------------------------------------
# Dispatcher integration — navigate verb
# ---------------------------------------------------------------------------


def test_navigate_verb_routes_through_actuator_with_mode() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate driving to paris",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out == ""
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["ok"] is True
    assert handler_meta["intent"] == "navigate"
    assert handler_meta["destination"] == "paris"
    assert handler_meta["mode"] == "driving"
    assert act.navigate_calls == [{"destination": "paris", "mode": "driving"}]


def test_navigate_verb_without_mode() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate to the white house",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out == ""
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["ok"] is True
    assert handler_meta["destination"] == "the white house"
    assert "mode" not in handler_meta
    assert act.navigate_calls == [
        {"destination": "the white house", "mode": None}
    ]


def test_navigate_verb_empty_args_returns_warning() -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.navigate_calls == []


def test_navigate_verb_mode_word_alone_returns_warning() -> None:
    """`zwingli navigate driving` consumes the mode token but leaves no
    destination — same shape as the empty-args case."""
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate driving",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.navigate_calls == []


def test_navigate_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_OPEN_URL})  # no navigate
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate paris",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert "not supported on this device" in out
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.navigate_calls == []


def test_navigate_verb_via_server_actuator_queues_navigate_action_with_mode() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli navigate walking to the library",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "navigate", "destination": "the library", "mode": "walking"}
    ]


def test_navigate_verb_via_server_actuator_omits_mode_key_when_absent() -> None:
    """A modeless navigate should NOT include a "mode": null in the
    payload — keeps the queued shape minimal for the Android side."""
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        "zwingli navigate paris",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "navigate", "destination": "paris"}
    ]


def test_navigate_via_server_actuator_respects_missing_capability() -> None:
    """When the Zwangli client advertises a cap list that omits 'navigate',
    the ServerActuator should refuse the call so the dispatcher's
    graceful-skip path runs."""
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator(capabilities={"open_url"})  # no navigate
    out, meta = tt.apply_transcript_triggers(
        "zwingli navigate paris",
        commands=_commands_for("navigate", "navigate"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert _intent_actions(act.client_actions) == []


# ---------------------------------------------------------------------------
# accessibility-global verbs (back / home / recents / notifications / quick_settings)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb,action",
    [
        ("back", "back"),
        ("home", "home"),
        ("recents", "recents"),
        ("notifications", "notifications"),
        ("quick_settings", "quick_settings"),
    ],
)
def test_accessibility_global_verb_routes_through_actuator(
    verb: str, action: str
) -> None:
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        f"zwingli {verb}",
        commands=_commands_for(verb, verb),
        actuator=act,
    )
    assert out == ""
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["ok"] is True
    assert handler_meta["intent"] == "accessibility_global"
    assert handler_meta["action"] == action
    assert act.accessibility_global_calls == [action]


def test_accessibility_global_verb_ignores_trailing_args() -> None:
    """`zwingli back now please` should still fire `back`. The verb takes
    no args; tolerating STT chatter avoids spurious bad-args paths."""
    act = InMemoryActuator()
    out, meta = tt.apply_transcript_triggers(
        "zwingli back now please",
        commands=_commands_for("back", "back"),
        actuator=act,
    )
    assert out == ""
    assert meta["meta"]["handler_meta"]["ok"] is True
    assert act.accessibility_global_calls == ["back"]


def test_accessibility_global_graceful_skip_when_capability_missing() -> None:
    bare = frozenset({CAP_OPEN_URL})  # no accessibility_global
    act = InMemoryActuator(caps=bare)
    out, meta = tt.apply_transcript_triggers(
        "zwingli home",
        commands=_commands_for("home", "home"),
        actuator=act,
    )
    assert out.startswith("⚠ zwingli:")
    assert "not supported on this device" in out
    assert meta["meta"]["handler_meta"]["ok"] is False
    assert act.accessibility_global_calls == []


@pytest.mark.parametrize(
    "verb,action",
    [
        ("back", "back"),
        ("home", "home"),
        ("recents", "recents"),
        ("notifications", "notifications"),
        ("quick_settings", "quick_settings"),
    ],
)
def test_accessibility_global_via_server_actuator_queues_action(
    verb: str, action: str
) -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    tt.apply_transcript_triggers(
        f"zwingli {verb}",
        commands=_commands_for(verb, verb),
        actuator=act,
    )
    assert _intent_actions(act.client_actions) == [
        {"type": "accessibility_global", "action": action}
    ]


def test_accessibility_global_factory_rejects_unknown_action() -> None:
    """Guards against silently registering a bogus action name."""
    from voicepipe.transcript_triggers._intents import (
        _make_accessibility_global_handler,
    )

    with pytest.raises(ValueError):
        _make_accessibility_global_handler("not_a_real_action")
