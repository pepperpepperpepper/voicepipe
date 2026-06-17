from __future__ import annotations

from voicepipe.transcript_triggers._actuator import (
    CAP_CALENDAR,
    InMemoryActuator,
)
from voicepipe.transcript_triggers._intents import _action_calendar


def test_calendar_emits_calendar_event_action() -> None:
    act = InMemoryActuator()
    out, meta = _action_calendar("dentist appointment", actuator=act)
    assert out == ""  # the action is the side effect; nothing typed
    assert meta == {"ok": True, "intent": "calendar_event", "title": "dentist appointment"}
    assert act.calendar_event_calls == ["dentist appointment"]


def test_calendar_empty_title_is_bad_args() -> None:
    act = InMemoryActuator()
    _out, meta = _action_calendar("   ", actuator=act)
    assert meta["ok"] is False and meta["error"] == "bad_args"
    assert act.calendar_event_calls == []


def test_calendar_graceful_skip_without_capability() -> None:
    # A device that doesn't advertise the calendar capability → unsupported,
    # not a crash (mirrors alarm/timer/dial on a bare client).
    act = InMemoryActuator(caps=frozenset())
    out, meta = _action_calendar("lunch with sam", actuator=act)
    assert meta["ok"] is False and meta["error"] == "capability_unsupported"
    assert out.startswith("⚠ zwingli")
    assert act.calendar_event_calls == []


def test_server_actuator_queues_calendar_client_action() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()  # defaults to all caps
    assert CAP_CALENDAR in act.capabilities()
    _action_calendar("team standup", actuator=act)
    assert act.client_actions == [{"type": "calendar_event", "title": "team standup"}]
