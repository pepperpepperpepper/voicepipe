from __future__ import annotations

from voicepipe.transcript_triggers._actuator import CAP_EMAIL, InMemoryActuator
from voicepipe.transcript_triggers._intents import _action_email, parse_email_args


def test_parse_email_args_keyed() -> None:
    assert parse_email_args("to=bob; subject=lunch; body=are you free") == (
        "bob", "lunch", "are you free",
    )


def test_parse_email_args_partial() -> None:
    assert parse_email_args("to=sam@example.com; body=ping") == ("sam@example.com", "", "ping")


def test_parse_email_args_bare_string_is_body() -> None:
    # No key=value pairs → the whole string is the body.
    assert parse_email_args("running ten minutes late") == ("", "", "running ten minutes late")


def test_email_emits_email_action() -> None:
    act = InMemoryActuator()
    out, meta = _action_email("to=bob; subject=lunch; body=are you free", actuator=act)
    assert out == ""
    assert meta == {"ok": True, "intent": "email", "to": "bob", "subject": "lunch", "body": "are you free"}
    assert act.email_calls == [{"to": "bob", "subject": "lunch", "body": "are you free"}]


def test_email_empty_is_bad_args() -> None:
    _out, meta = _action_email("   ", actuator=InMemoryActuator())
    assert meta["ok"] is False and meta["error"] == "bad_args"


def test_email_graceful_skip_without_capability() -> None:
    act = InMemoryActuator(caps=frozenset())
    out, meta = _action_email("to=x; body=hi", actuator=act)
    assert meta["error"] == "capability_unsupported"
    assert act.email_calls == []


def test_server_actuator_queues_email_action_omitting_empty_fields() -> None:
    from voicepipe.dispatch_server import ServerActuator

    act = ServerActuator()
    assert CAP_EMAIL in act.capabilities()
    _action_email("to=bob; body=hi", actuator=act)  # no subject
    assert act.client_actions == [{"type": "email", "to": "bob", "body": "hi"}]
