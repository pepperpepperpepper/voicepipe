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


def test_server_actuator_set_alarm_relative_queues_in_seconds() -> None:
    act = ServerActuator()
    assert act.set_alarm(None, None, "standup", in_seconds=120) is True
    assert act.set_alarm(None, None, in_seconds=90) is True
    assert act.client_actions == [
        {"type": "set_alarm", "in_seconds": 120, "message": "standup"},
        {"type": "set_alarm", "in_seconds": 90},
    ]


def test_server_actuator_set_alarm_relative_rejects_out_of_range() -> None:
    act = ServerActuator()
    assert act.set_alarm(None, None, in_seconds=0) is False
    assert act.set_alarm(None, None, in_seconds=86_401) is False
    assert act.client_actions == []


def test_server_actuator_call_business_inline_resolves_when_no_resolve_cap(
    monkeypatch,
) -> None:
    import voicepipe.serper_client as serper_client

    monkeypatch.setattr(
        serper_client,
        "lookup_place",
        lambda q, **k: {
            "name": "The Sukhothai Shanghai",
            "phone": "+86 21 5237 8888",
            "address": "380 Weihai Rd",
        },
    )
    # Client supports dial but NOT resolve_dial → server resolves inline.
    act = ServerActuator(capabilities={"dial"})
    assert act.call_business("Sukhothai Hotel Shanghai") is True
    assert act.client_actions == [
        {
            "type": "dial",
            "number": "+86 21 5237 8888",
            "label": "The Sukhothai Shanghai",
            "address": "380 Weihai Rd",
        }
    ]


def test_server_actuator_call_business_defers_to_client_when_resolve_cap() -> None:
    # Client advertises resolve_dial → server hands back the query for the
    # client's two-step (status → /resolve-call → dial). No Serper call here.
    act = ServerActuator(capabilities={"dial", "resolve_dial"})
    assert act.call_business("Sukhothai Hotel Shanghai") is True
    assert act.client_actions == [
        {"type": "resolve_dial", "query": "Sukhothai Hotel Shanghai"}
    ]


def test_server_actuator_call_business_no_phone_returns_false(monkeypatch) -> None:
    import voicepipe.serper_client as serper_client

    monkeypatch.setattr(
        serper_client, "lookup_place", lambda q, **k: {"name": "X", "phone": "", "address": ""}
    )
    act = ServerActuator(capabilities={"dial"})  # inline path
    assert act.call_business("place with no phone") is False
    assert act.client_actions == []


def test_server_actuator_call_business_requires_dial_capability(monkeypatch) -> None:
    act = ServerActuator(capabilities=set())  # no dial
    assert act.call_business("anything") is False


def test_resolve_call_endpoint_returns_number(client, monkeypatch) -> None:
    import voicepipe.serper_client as serper_client

    monkeypatch.setattr(
        serper_client,
        "lookup_place",
        lambda q, **k: {
            "name": "The Sukhothai Shanghai",
            "phone": "+86 21 5237 8888",
            "address": "380 Weihai Rd",
        },
    )
    resp = client.post("/resolve-call", json={"query": "Sukhothai Hotel Shanghai"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "number": "+86 21 5237 8888",
        "name": "The Sukhothai Shanghai",
        "address": "380 Weihai Rd",
    }


def test_resolve_call_endpoint_not_found(client, monkeypatch) -> None:
    import voicepipe.serper_client as serper_client

    monkeypatch.setattr(serper_client, "lookup_place", lambda q, **k: None)
    resp = client.post("/resolve-call", json={"query": "nope"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "not_found"}


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


# ---------------------------------------------------------------------------
# PATCH /triggers — mutate activation phrases over HTTP
# ---------------------------------------------------------------------------


import json as _json
from pathlib import Path as _Path


def _write_triggers_file(path: _Path, triggers: dict[str, dict[str, str]]) -> _Path:
    payload = {
        "version": 1,
        "triggers": triggers,
        "verbs": {"strip": {"type": "builtin"}},
    }
    path.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
    return path


@pytest.fixture
def triggers_file_client(tmp_path: _Path, monkeypatch) -> tuple[TestClient, _Path]:
    """Spin a client whose `triggers_json_path()` points at a temp file.

    Unlike ``patch_commands`` (which mocks the in-memory loader), this
    fixture lets the PATCH handler actually read and write disk — that
    is the whole point of the endpoint.
    """
    cfg = _write_triggers_file(
        tmp_path / "triggers.json",
        {"zwingli": {"action": "dispatch"}, "zwingly": {"action": "dispatch"}},
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    return TestClient(create_app()), cfg


@pytest.fixture
def triggers_file_client_with_token(
    tmp_path: _Path, monkeypatch
) -> tuple[TestClient, _Path, str]:
    cfg = _write_triggers_file(
        tmp_path / "triggers.json",
        {"zwingli": {"action": "dispatch"}},
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    token = "patch-test-token"
    return TestClient(create_app(token=token)), cfg, token


def test_patch_triggers_adds_a_phrase(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["triggers"] == ["computer", "zwingli", "zwingly"]
    # File reflects the mutation.
    written = _json.loads(cfg.read_text())
    assert written["triggers"]["computer"] == {"action": "dispatch"}


def test_patch_triggers_removes_a_phrase(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"remove": ["zwingly"]})
    assert r.status_code == 200, r.text
    assert r.json()["triggers"] == ["zwingli"]
    written = _json.loads(cfg.read_text())
    assert "zwingly" not in written["triggers"]
    assert "zwingli" in written["triggers"]


def test_patch_triggers_combined_add_and_remove(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch(
        "/triggers", json={"add": ["computer", "hey there"], "remove": ["zwingly"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["triggers"] == ["computer", "hey there", "zwingli"]


def test_patch_triggers_normalizes_case_and_whitespace(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"add": ["  Hey  Computer  "]})
    assert r.status_code == 200, r.text
    assert "hey computer" in r.json()["triggers"]


def test_patch_triggers_add_existing_is_idempotent(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"add": ["zwingli", "ZWINGLY"]})
    assert r.status_code == 200, r.text
    # Triggers count unchanged.
    assert r.json()["triggers"] == ["zwingli", "zwingly"]


def test_patch_triggers_remove_absent_is_idempotent(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"remove": ["never_added"]})
    # Non-existent phrase isn't a validation failure — silent no-op.
    # ('never_added' has underscore so it would fail validate_phrase if it
    # were going through 'add', but for 'remove' we only require non-empty.)
    assert r.status_code == 200, r.text
    assert r.json()["triggers"] == ["zwingli", "zwingly"]


def test_patch_triggers_rejects_invalid_phrase(triggers_file_client) -> None:
    client, cfg = triggers_file_client
    r = client.patch("/triggers", json={"add": ["bad-phrase", "ok"]})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "invalid_phrase"
    failures = {f["phrase"]: f["reason"] for f in body["detail"]["failures"]}
    assert "bad-phrase" in failures
    # The whole patch is rejected — no partial write.
    written = _json.loads(cfg.read_text())
    assert list(written["triggers"]) == ["zwingli", "zwingly"]


def test_patch_triggers_rejects_too_short(triggers_file_client) -> None:
    client, _ = triggers_file_client
    r = client.patch("/triggers", json={"add": ["x"]})
    assert r.status_code == 400
    assert "too short" in r.json()["detail"]["failures"][0]["reason"]


def test_patch_triggers_rejects_conflict_between_add_and_remove(
    triggers_file_client,
) -> None:
    client, cfg = triggers_file_client
    r = client.patch(
        "/triggers", json={"add": ["computer"], "remove": ["Computer"]}
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"] == "conflict"
    assert body["detail"]["overlapping"] == ["computer"]
    # No write.
    written = _json.loads(cfg.read_text())
    assert "computer" not in written["triggers"]


def test_patch_triggers_refuses_to_remove_last(tmp_path: _Path, monkeypatch) -> None:
    cfg = _write_triggers_file(
        tmp_path / "triggers.json", {"zwingli": {"action": "dispatch"}}
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    client = TestClient(create_app())
    r = client.patch("/triggers", json={"remove": ["zwingli"]})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "would_remove_all_triggers"
    # File untouched.
    written = _json.loads(cfg.read_text())
    assert list(written["triggers"]) == ["zwingli"]


def test_patch_triggers_returns_500_when_file_missing(
    tmp_path: _Path, monkeypatch
) -> None:
    missing = tmp_path / "no-such.json"
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: missing
    )
    client = TestClient(create_app())
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "triggers_json_missing"


def test_patch_triggers_returns_500_when_file_malformed(
    tmp_path: _Path, monkeypatch
) -> None:
    cfg = tmp_path / "triggers.json"
    cfg.write_text("{this is not json", encoding="utf-8")
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    client = TestClient(create_app())
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "triggers_json_unreadable"


def test_patch_triggers_requires_auth_when_token_set(
    triggers_file_client_with_token,
) -> None:
    client, _, _ = triggers_file_client_with_token
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 401


def test_patch_triggers_accepts_correct_bearer(
    triggers_file_client_with_token,
) -> None:
    client, cfg, token = triggers_file_client_with_token
    r = client.patch(
        "/triggers",
        json={"add": ["computer"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    written = _json.loads(cfg.read_text())
    assert "computer" in written["triggers"]


def test_patch_triggers_rejects_wrong_bearer(
    triggers_file_client_with_token,
) -> None:
    client, _, _ = triggers_file_client_with_token
    r = client.patch(
        "/triggers",
        json={"add": ["computer"]},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


def test_patch_triggers_preserves_other_top_level_keys(
    tmp_path: _Path, monkeypatch
) -> None:
    cfg = tmp_path / "triggers.json"
    cfg.write_text(
        _json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {"strip": {"type": "builtin"}, "custom": {"type": "shell"}},
                "llm_profiles": {"foo": {"temperature": 0.7}},
                "dispatch": {"unknown_verb": "strip"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    client = TestClient(create_app())
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 200, r.text
    written = _json.loads(cfg.read_text())
    assert written["verbs"]["custom"] == {"type": "shell"}
    assert written["llm_profiles"] == {"foo": {"temperature": 0.7}}
    assert written["dispatch"] == {"unknown_verb": "strip"}


def test_patch_triggers_get_reflects_mutation_after_cache_invalidates(
    tmp_path: _Path, monkeypatch
) -> None:
    """The GET /triggers cache is mtime-invalidated, so PATCH should be
    visible on the very next GET without a server restart."""
    # Reset both caches so they have nothing stale from earlier tests.
    import voicepipe.config as cfgmod

    cfgmod._TRANSCRIPT_COMMANDS_JSON_CACHE = None
    cfgmod._TRIGGERS_JSON_CACHE = None
    cfg = _write_triggers_file(
        tmp_path / "triggers.json", {"zwingli": {"action": "dispatch"}}
    )
    monkeypatch.setattr(
        "voicepipe.dispatch_server.triggers_json_path", lambda: cfg
    )
    monkeypatch.setattr("voicepipe.config.triggers_json_path", lambda: cfg)
    client = TestClient(create_app())
    r = client.patch("/triggers", json={"add": ["computer"]})
    assert r.status_code == 200, r.text
    g = client.get("/triggers")
    assert g.status_code == 200
    assert "computer" in g.json()["triggers"]
    assert "zwingli" in g.json()["triggers"]


def test_patch_triggers_with_no_changes_is_ok(triggers_file_client) -> None:
    """Empty add+remove is a no-op — useful as a 'commit zero changes' ping
    to confirm the endpoint is reachable and the token is good."""
    client, _ = triggers_file_client
    r = client.patch("/triggers", json={})
    assert r.status_code == 200, r.text
    assert r.json()["triggers"] == ["zwingli", "zwingly"]
