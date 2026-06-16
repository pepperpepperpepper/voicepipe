"""Google Sign-In ID-token verification + single-email allowlist at _check_auth."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("google.auth.transport.requests")  # google-auth[requests]

from fastapi.testclient import TestClient  # noqa: E402

import voicepipe.config as config  # noqa: E402
import voicepipe.dispatch_server as ds  # noqa: E402

if ds._google_id_token is None:  # pragma: no cover
    pytest.skip("google-auth not importable", allow_module_level=True)

ALLOWED = "peppersclothescult@gmail.com"
CLIENT_ID = "test-web-client.apps.googleusercontent.com"


def _commands() -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip", error_destination="type"),
        verbs={"strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin")},
    )


@pytest.fixture(autouse=True)
def _patch_commands(monkeypatch):
    monkeypatch.setattr(ds, "get_transcript_commands_config", lambda load_env=False: _commands())


def _client(monkeypatch, *, client_id=CLIENT_ID, allowed=ALLOWED,
            static_token=None, allow_static=None, verify=None) -> TestClient:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", client_id or "")
    monkeypatch.setenv("ZWANGLI_ALLOWED_EMAIL", allowed or "")
    if static_token is not None:
        monkeypatch.setenv("VOICEPIPE_DISPATCH_TOKEN", static_token)
    else:
        monkeypatch.delenv("VOICEPIPE_DISPATCH_TOKEN", raising=False)
    if allow_static is not None:
        monkeypatch.setenv("ZWANGLI_ALLOW_STATIC_TOKEN", allow_static)
    else:
        monkeypatch.delenv("ZWANGLI_ALLOW_STATIC_TOKEN", raising=False)
    if verify is not None:
        monkeypatch.setattr(ds._google_id_token, "verify_oauth2_token", verify)
    return TestClient(ds.create_app())


def _ok_verify(email=ALLOWED, verified=True):
    def _v(token, request, audience):
        assert audience == CLIENT_ID  # aud must be our client id
        return {"email": email, "email_verified": verified, "iss": "accounts.google.com"}
    return _v


def _raise_verify(token, request, audience):
    raise ValueError("invalid token")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_reports_google_auth(monkeypatch):
    d = _client(monkeypatch, verify=_ok_verify()).get("/health").json()
    assert d["auth_required"] is True and d["google_auth"] is True


def test_allowed_email_passes(monkeypatch):
    c = _client(monkeypatch, verify=_ok_verify())
    assert c.get("/triggers", headers=_bearer("a-jwt")).status_code == 200


def test_wrong_email_is_403(monkeypatch):
    c = _client(monkeypatch, verify=_ok_verify(email="intruder@gmail.com"))
    assert c.get("/triggers", headers=_bearer("a-jwt")).status_code == 403


def test_unverified_email_is_403(monkeypatch):
    c = _client(monkeypatch, verify=_ok_verify(verified=False))
    assert c.get("/triggers", headers=_bearer("a-jwt")).status_code == 403


def test_invalid_token_is_401(monkeypatch):
    c = _client(monkeypatch, verify=_raise_verify)
    assert c.get("/triggers", headers=_bearer("garbage")).status_code == 401


def test_missing_header_is_401(monkeypatch):
    c = _client(monkeypatch, verify=_ok_verify())
    assert c.get("/triggers").status_code == 401


def test_email_match_is_case_insensitive(monkeypatch):
    c = _client(monkeypatch, verify=_ok_verify(email="PeppersClothesCult@Gmail.com"))
    assert c.get("/triggers", headers=_bearer("a-jwt")).status_code == 200


def test_static_token_off_by_default_when_google_enabled(monkeypatch):
    # Google on + ZWANGLI_ALLOW_STATIC_TOKEN unset → the static bearer is NOT honored.
    c = _client(monkeypatch, static_token="sekret", verify=_raise_verify)
    assert c.get("/triggers", headers=_bearer("sekret")).status_code == 401


def test_static_token_breakglass_when_explicitly_enabled(monkeypatch):
    c = _client(monkeypatch, static_token="sekret", allow_static="1", verify=_raise_verify)
    assert c.get("/triggers", headers=_bearer("sekret")).status_code == 200


def test_static_only_when_google_unconfigured(monkeypatch):
    # No client ID → pre-Google deploy: static bearer honored by default.
    c = _client(monkeypatch, client_id="", static_token="sekret")
    assert c.get("/triggers", headers=_bearer("sekret")).status_code == 200
    assert c.get("/triggers", headers=_bearer("wrong")).status_code == 401
