"""Tests for the AWS Lambda adapter (warm-ping short-circuit + delegation)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mangum")

import voicepipe.aws_lambda as lam  # noqa: E402


def test_keep_warm_input_short_circuits(monkeypatch) -> None:
    # If a warm ping ever reached Mangum it would blow up (no HTTP event),
    # so make delegation explode to prove we never get there.
    monkeypatch.setattr(
        lam, "_asgi_handler",
        lambda *a, **k: pytest.fail("warm ping must not reach Mangum"),
    )
    assert lam.handler({"warmer": True}, None) == {"warmed": True}


def test_raw_scheduled_event_short_circuits(monkeypatch) -> None:
    monkeypatch.setattr(
        lam, "_asgi_handler",
        lambda *a, **k: pytest.fail("scheduled event must not reach Mangum"),
    )
    event = {"source": "aws.events", "detail-type": "Scheduled Event"}
    assert lam.handler(event, None) == {"warmed": True}


def test_http_event_delegates_to_mangum(monkeypatch) -> None:
    seen = {}

    def fake_asgi(event, context):
        seen["event"] = event
        return {"statusCode": 200}

    monkeypatch.setattr(lam, "_asgi_handler", fake_asgi)
    http_event = {"requestContext": {"http": {"method": "POST", "path": "/health"}}}
    out = lam.handler(http_event, None)
    assert out == {"statusCode": 200}
    assert seen["event"] is http_event


def test_is_keep_warm_predicate() -> None:
    assert lam._is_keep_warm({"warmer": True}) is True
    assert lam._is_keep_warm({"source": "aws.events"}) is True
    assert lam._is_keep_warm({"warmer": False}) is False
    assert lam._is_keep_warm({"requestContext": {}}) is False
    assert lam._is_keep_warm("not-a-dict") is False
