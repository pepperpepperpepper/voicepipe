"""Tests for POST /transcribe-dispatch (audio-in / actions-out)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import voicepipe.config as config  # noqa: E402
from voicepipe.dispatch_server import create_app  # noqa: E402
from voicepipe.transcription import TranscriptionError  # noqa: E402

_OCTET = {"Content-Type": "application/octet-stream"}


def _make_commands() -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination="type"
        ),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip", enabled=True, type="builtin"
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
    commands = _make_commands()
    monkeypatch.setattr(
        "voicepipe.dispatch_server.get_transcript_commands_config",
        lambda load_env=False: commands,
    )
    return commands


@pytest.fixture
def client(patch_commands) -> TestClient:
    return TestClient(create_app())


def _stub_stt(monkeypatch, *, returns: str = "zwingli copy hello") -> dict:
    """Replace the (lazily-imported) STT call; capture its kwargs."""
    captured: dict = {}

    def fake_stt(audio_bytes, *, filename, model, language=None,
                 apply_triggers=True, **kwargs):
        captured.update(
            audio=audio_bytes,
            filename=filename,
            model=model,
            language=language,
            apply_triggers=apply_triggers,
        )
        return returns

    monkeypatch.setattr("voicepipe.transcription.transcribe_audio_bytes", fake_stt)
    return captured


def test_wires_stt_into_the_same_dispatcher(client, monkeypatch) -> None:
    captured = _stub_stt(monkeypatch, returns="zwingli go home now")

    def fake_dispatch(transcript, *, commands, actuator):
        actuator.client_actions.append({"type": "web_search", "query": transcript})
        return f"out:{transcript}", {"ok": True, "planner": "test"}

    monkeypatch.setattr(
        "voicepipe.dispatch_server.tt.apply_transcript_triggers", fake_dispatch
    )

    resp = client.post("/transcribe-dispatch", content=b"RIFFfakeaudio", headers=_OCTET)

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "zwingli go home now"
    assert data["output_text"] == "out:zwingli go home now"
    assert data["client_actions"] == [
        {"type": "web_search", "query": "zwingli go home now"}
    ]
    assert data["ok"] is True
    # The endpoint must hand STT the raw bytes and dispatch separately.
    assert captured["audio"] == b"RIFFfakeaudio"
    assert captured["apply_triggers"] is False


def test_defaults_to_groq_whisper_turbo(client, monkeypatch) -> None:
    captured = _stub_stt(monkeypatch)
    resp = client.post("/transcribe-dispatch", content=b"x", headers=_OCTET)
    assert resp.status_code == 200
    assert captured["model"] == "groq:whisper-large-v3-turbo"


def test_model_query_param_overrides_default(client, monkeypatch) -> None:
    captured = _stub_stt(monkeypatch)
    resp = client.post(
        "/transcribe-dispatch",
        content=b"x",
        headers=_OCTET,
        params={"model": "openai:gpt-4o-transcribe", "language": "en"},
    )
    assert resp.status_code == 200
    assert captured["model"] == "openai:gpt-4o-transcribe"
    assert captured["language"] == "en"


def test_env_default_stt_model(client, monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_DISPATCH_STT_MODEL", "openai:gpt-4o-mini-transcribe")
    captured = _stub_stt(monkeypatch)
    resp = client.post("/transcribe-dispatch", content=b"x", headers=_OCTET)
    assert resp.status_code == 200
    assert captured["model"] == "openai:gpt-4o-mini-transcribe"


def test_empty_body_is_rejected(client, monkeypatch) -> None:
    _stub_stt(monkeypatch)
    resp = client.post("/transcribe-dispatch", content=b"", headers=_OCTET)
    assert resp.status_code == 400


def test_oversize_audio_returns_413(client, monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_DISPATCH_MAX_AUDIO_BYTES", "8")
    _stub_stt(monkeypatch)
    resp = client.post("/transcribe-dispatch", content=b"0123456789", headers=_OCTET)
    assert resp.status_code == 413
    assert resp.json()["detail"]["error"] == "audio_too_large"


def test_stt_failure_returns_502(client, monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise TranscriptionError("groq exploded")

    monkeypatch.setattr("voicepipe.transcription.transcribe_audio_bytes", boom)
    resp = client.post("/transcribe-dispatch", content=b"x", headers=_OCTET)
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "transcription_failed"


def test_requires_bearer_token_when_configured(patch_commands, monkeypatch) -> None:
    _stub_stt(monkeypatch)
    authed = TestClient(create_app(token="secret-token"))

    missing = authed.post("/transcribe-dispatch", content=b"x", headers=_OCTET)
    assert missing.status_code == 401

    ok = authed.post(
        "/transcribe-dispatch",
        content=b"x",
        headers={**_OCTET, "Authorization": "Bearer secret-token"},
    )
    assert ok.status_code == 200


def test_real_dispatcher_smoke(client, monkeypatch) -> None:
    # Only STT is stubbed; the real dispatcher runs on the fixture config.
    _stub_stt(monkeypatch, returns="zwingli copy hello world")
    resp = client.post("/transcribe-dispatch", content=b"x", headers=_OCTET)
    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "zwingli copy hello world"
    assert data["ok"] is True
