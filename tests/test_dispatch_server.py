"""Tests for the FastAPI dispatch server (Phase 2)."""
from __future__ import annotations

import os

import pytest

# Skip the whole module on systems without the server extra installed.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import voicepipe.config as config  # noqa: E402
import voicepipe.transcript_triggers as tt  # noqa: E402
from voicepipe.dispatch_server import (  # noqa: E402
    ServerActuator,
    _ALL_CAPS,
    _resolve_token,
    create_app,
    run,
)
from voicepipe.transcript_triggers._actuator import (  # noqa: E402
    CAP_AUDIO_FEEDBACK,
    CAP_CLIPBOARD,
    CAP_DIAL,
    CAP_OPEN_URL,
    CAP_SET_ALARM,
    CAP_SET_TIMER,
    CAP_SUBPROCESS,
    CAP_WEB_SEARCH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_commands() -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch", "zwingly": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination="type"
        ),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip", enabled=True, type="builtin"
            ),
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell"
            ),
            "copy": config.TranscriptVerbConfig(
                action="clipboard",
                enabled=True,
                type="builtin",
                destination="clipboard",
            ),
        },
    )


@pytest.fixture
def patch_commands(monkeypatch) -> config.TranscriptCommandsConfig:
    """Make get_transcript_commands_config return a known fixture."""
    commands = _make_commands()
    monkeypatch.setattr(
        "voicepipe.dispatch_server.get_transcript_commands_config",
        lambda load_env=False: commands,
    )
    return commands


@pytest.fixture
def client(patch_commands) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def client_with_token(patch_commands) -> tuple[TestClient, str]:
    token = "test-secret-token"
    return TestClient(create_app(token=token)), token


# ---------------------------------------------------------------------------
# ServerActuator unit behaviour
# ---------------------------------------------------------------------------


def test_server_actuator_defaults_to_all_capabilities() -> None:
    act = ServerActuator()
    assert act.capabilities() == _ALL_CAPS


def test_server_actuator_filters_capabilities() -> None:
    act = ServerActuator(capabilities={CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK})
    assert act.capabilities() == frozenset({CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK})
    assert CAP_SUBPROCESS not in act.capabilities()


def test_server_actuator_clipboard_queues_client_action() -> None:
    act = ServerActuator()
    assert act.set_clipboard("hello") is True
    assert act.client_actions == [{"type": "clipboard", "text": "hello"}]


def test_server_actuator_feedback_queues_client_action() -> None:
    act = ServerActuator()
    act.play_feedback("zwingli.match")
    assert act.client_actions == [{"type": "feedback", "event": "zwingli.match"}]


def test_server_actuator_clipboard_noops_when_capability_missing() -> None:
    act = ServerActuator(capabilities={CAP_SUBPROCESS})
    assert act.set_clipboard("hello") is False
    assert act.client_actions == []


def test_server_actuator_filters_unknown_capability_strings() -> None:
    act = ServerActuator(capabilities={"clipboard", "made_up_thing"})
    assert act.capabilities() == frozenset({CAP_CLIPBOARD})


def test_server_actuator_advertises_all_intent_caps_by_default() -> None:
    """ServerActuator() with no caps argument defaults to _ALL_CAPS, which
    now includes the five Intent-style verbs. Each must be present so the
    dispatcher doesn't graceful-skip them when no client caps are sent."""
    caps = ServerActuator().capabilities()
    assert CAP_WEB_SEARCH in caps
    assert CAP_OPEN_URL in caps
    assert CAP_SET_ALARM in caps
    assert CAP_SET_TIMER in caps
    assert CAP_DIAL in caps


def test_server_actuator_web_search_queues_client_action() -> None:
    act = ServerActuator()
    assert act.web_search("weather tokyo") is True
    assert act.client_actions == [{"type": "web_search", "query": "weather tokyo"}]


def test_server_actuator_open_url_queues_client_action() -> None:
    act = ServerActuator()
    assert act.open_url("https://example.com/") is True
    assert act.client_actions == [
        {"type": "open_url", "url": "https://example.com/"}
    ]


def test_server_actuator_set_alarm_queues_with_optional_message() -> None:
    act = ServerActuator()
    assert act.set_alarm(7, 30, "wake up") is True
    assert act.set_alarm(6, 0) is True
    assert act.client_actions == [
        {"type": "set_alarm", "hour": 7, "minutes": 30, "message": "wake up"},
        {"type": "set_alarm", "hour": 6, "minutes": 0},
    ]


def test_server_actuator_set_alarm_rejects_out_of_range() -> None:
    act = ServerActuator()
    assert act.set_alarm(24, 0) is False
    assert act.set_alarm(-1, 0) is False
    assert act.set_alarm(0, 60) is False
    assert act.set_alarm(0, -1) is False
    assert act.client_actions == []


def test_server_actuator_set_timer_queues_with_optional_message() -> None:
    act = ServerActuator()
    assert act.set_timer(300, "pasta") is True
    assert act.set_timer(60) is True
    assert act.client_actions == [
        {"type": "set_timer", "seconds": 300, "message": "pasta"},
        {"type": "set_timer", "seconds": 60},
    ]


def test_server_actuator_set_timer_rejects_out_of_range() -> None:
    act = ServerActuator()
    assert act.set_timer(0) is False
    assert act.set_timer(86_401) is False
    assert act.client_actions == []


def test_server_actuator_dial_queues_client_action() -> None:
    act = ServerActuator()
    assert act.dial("+15555550100") is True
    assert act.client_actions == [{"type": "dial", "number": "+15555550100"}]


def test_server_actuator_intent_verbs_noop_when_capabilities_missing() -> None:
    """A client that only advertises clipboard/audio_feedback (existing
    behavior pre-Phase A) must NOT receive Intent-style client_actions."""
    bare = {CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK}
    act = ServerActuator(capabilities=bare)
    assert act.web_search("q") is False
    assert act.open_url("https://x") is False
    assert act.set_alarm(7, 0) is False
    assert act.set_timer(60) is False
    assert act.dial("+1") is False
    assert act.client_actions == []


def test_server_actuator_intent_verbs_reject_blank_input() -> None:
    act = ServerActuator()
    assert act.web_search("") is False
    assert act.web_search("   ") is False
    assert act.open_url("") is False
    assert act.dial("") is False
    assert act.client_actions == []


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


def test_health_does_not_require_auth(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["auth_required"] is False


def test_health_advertises_auth_required_when_token_set(client_with_token) -> None:
    cli, _token = client_with_token
    response = cli.get("/health")
    assert response.status_code == 200
    assert response.json()["auth_required"] is True


# ---------------------------------------------------------------------------
# /dispatch
# ---------------------------------------------------------------------------


def test_dispatch_passthrough_when_no_trigger(client) -> None:
    response = client.post("/dispatch", json={"transcript": "just plain dictation"})
    assert response.status_code == 200
    body = response.json()
    assert body["output_text"] == "just plain dictation"
    # No trigger matched → payload is None, no client actions.
    assert body["payload"] is None
    assert body["client_actions"] == []


def test_dispatch_strip_verb(client) -> None:
    response = client.post(
        "/dispatch", json={"transcript": "zwingli strip alpha bravo"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["output_text"] == "alpha bravo"
    # No clipboard side-effect — audio_feedback hook may queue a success
    # event but no clipboard work happened here.
    clipboard_actions = [
        a for a in body["client_actions"] if a["type"] == "clipboard"
    ]
    assert clipboard_actions == []


def test_dispatch_shell_verb_runs_server_side(monkeypatch, client) -> None:
    """The shell verb runs on the server (that's the whole point of the
    server — give the phone a shell to drive)."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    class _FakeProc:
        returncode = 0
        stdout = "server-side-output\n"
        stderr = ""

    monkeypatch.setattr(tt.subprocess, "run", lambda *a, **k: _FakeProc())

    response = client.post(
        "/dispatch", json={"transcript": "zwingli subprocess echo hi"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["output_text"] == "server-side-output"


def test_dispatch_client_without_subprocess_capability_graceful_skips_shell(
    monkeypatch, client
) -> None:
    """A phone without a shell advertises capabilities=['clipboard',
    'audio_feedback']. The server's actuator drops CAP_SUBPROCESS, the
    dispatcher graceful-skips the shell verb, and the user sees a polite
    error instead of the server quietly running rm -rf for them."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    response = client.post(
        "/dispatch",
        json={
            "transcript": "zwingli subprocess ls -la",
            "capabilities": ["clipboard", "audio_feedback"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["output_text"].startswith("⚠ zwingli:")
    assert "not supported on this device" in body["output_text"]


def test_dispatch_error_clipboard_destination_queues_client_action(
    monkeypatch,
) -> None:
    """error_destination=clipboard should queue a client_actions entry so
    the calling client can put the error in *their* clipboard."""
    base = _make_commands()
    commands = config.TranscriptCommandsConfig(
        triggers=dict(base.triggers),
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb=base.dispatch.unknown_verb,
            error_destination="clipboard",
        ),
        verbs=dict(base.verbs),
        llm_profiles=dict(base.llm_profiles),
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.get_transcript_commands_config",
        lambda load_env=False: commands,
    )
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "0")  # force shell error

    cli = TestClient(create_app())
    response = cli.post(
        "/dispatch", json={"transcript": "zwingli subprocess rm -rf /"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["output_text"].startswith("⚠ zwingli:")
    clipboard_actions = [
        a for a in body["client_actions"] if a["type"] == "clipboard"
    ]
    assert clipboard_actions == [{"type": "clipboard", "text": body["output_text"]}]


def test_dispatch_session_id_is_accepted_and_ignored(client) -> None:
    """The session_id field is reserved for Phase 3+ multi-client support;
    Phase 2 just accepts and ignores it."""
    response = client.post(
        "/dispatch",
        json={"transcript": "zwingli strip alpha", "session_id": "phone-42"},
    )
    assert response.status_code == 200
    assert response.json()["output_text"] == "alpha"


# ---------------------------------------------------------------------------
# /triggers
# ---------------------------------------------------------------------------


def test_triggers_endpoint_returns_resolved_config(client) -> None:
    response = client.get("/triggers")
    assert response.status_code == 200
    body = response.json()
    assert body["triggers"] == {"zwingli": "dispatch", "zwingly": "dispatch"}
    assert "subprocess" in body["verbs"]
    assert body["verbs"]["subprocess"]["action"] == "shell"
    assert body["dispatch"]["unknown_verb"] == "strip"


# ---------------------------------------------------------------------------
# /log/tail
# ---------------------------------------------------------------------------


def test_log_tail_empty_when_no_log_file(monkeypatch, client, tmp_path) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE", str(tmp_path / "nope.log"))
    response = client.get("/log/tail")
    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []


def test_log_tail_returns_recent_events(monkeypatch, client, tmp_path) -> None:
    log_file = tmp_path / "zwingli.log"
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE", str(log_file))

    # Trigger a dispatch so the log fills with at least one event.
    client.post("/dispatch", json={"transcript": "zwingli strip foo"})

    response = client.get("/log/tail?n=10")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["events"], list)
    assert len(body["events"]) >= 1
    event_names = {ev.get("event") for ev in body["events"]}
    assert "trigger_match" in event_names


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_dispatch_rejected_without_bearer_when_token_set(client_with_token) -> None:
    cli, _ = client_with_token
    response = cli.post("/dispatch", json={"transcript": "zwingli strip x"})
    assert response.status_code == 401


def test_dispatch_rejected_with_wrong_bearer(client_with_token) -> None:
    cli, _ = client_with_token
    response = cli.post(
        "/dispatch",
        json={"transcript": "zwingli strip x"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_dispatch_accepts_correct_bearer(client_with_token) -> None:
    cli, token = client_with_token
    response = cli.post(
        "/dispatch",
        json={"transcript": "zwingli strip alpha"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["output_text"] == "alpha"


def test_triggers_and_log_also_require_bearer_when_token_set(
    client_with_token,
) -> None:
    cli, token = client_with_token
    assert cli.get("/triggers").status_code == 401
    assert cli.get("/log/tail").status_code == 401
    # With the right header, both succeed.
    headers = {"Authorization": f"Bearer {token}"}
    assert cli.get("/triggers", headers=headers).status_code == 200
    assert cli.get("/log/tail", headers=headers).status_code == 200


# ---------------------------------------------------------------------------
# Token resolution + run() bind guard
# ---------------------------------------------------------------------------


def test_resolve_token_prefers_explicit_argument(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_DISPATCH_TOKEN", "from-env")
    assert _resolve_token("explicit") == "explicit"


def test_resolve_token_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_DISPATCH_TOKEN", "from-env")
    assert _resolve_token(None) == "from-env"


def test_resolve_token_returns_none_when_neither_set(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_DISPATCH_TOKEN", raising=False)
    assert _resolve_token(None) is None


def test_run_refuses_non_loopback_bind_without_token(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_DISPATCH_TOKEN", raising=False)
    # Don't actually start uvicorn; the bind-guard fires before that.
    with pytest.raises(RuntimeError, match="Refusing to bind"):
        run(host="0.0.0.0", port=8765)


def test_run_allows_loopback_bind_without_token(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_DISPATCH_TOKEN", raising=False)
    # Stop before uvicorn.run actually binds — assert it gets that far without
    # raising the bind-guard error.
    called = {}

    def _fake_uvicorn_run(app, *, host, port, **kw):
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr("uvicorn.run", _fake_uvicorn_run)
    run(host="127.0.0.1", port=12345)
    assert called == {"host": "127.0.0.1", "port": 12345}
