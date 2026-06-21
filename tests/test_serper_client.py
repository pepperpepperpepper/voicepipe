from __future__ import annotations

import io
import json

import pytest

from voicepipe import serper_client


def _fake_urlopen(payload: dict):
    """Return a urlopen replacement yielding `payload` as JSON."""

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    def _open(req, timeout=None):  # noqa: ANN001
        return _Resp(json.dumps(payload).encode("utf-8"))

    return _open


def test_lookup_place_extracts_top_result(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(
        serper_client.urllib.request,
        "urlopen",
        _fake_urlopen(
            {
                "places": [
                    {
                        "title": "The Sukhothai Shanghai",
                        "phoneNumber": "+86 21 5237 8888",
                        "address": "380 Weihai Rd, Shanghai",
                    }
                ]
            }
        ),
    )
    place = serper_client.lookup_place("Sukhothai Hotel Shanghai")
    assert place == {
        "name": "The Sukhothai Shanghai",
        "phone": "+86 21 5237 8888",
        "address": "380 Weihai Rd, Shanghai",
    }
    assert serper_client.lookup_phone("Sukhothai Hotel Shanghai") == "+86 21 5237 8888"


def test_lookup_place_no_results_returns_none(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")
    monkeypatch.setattr(
        serper_client.urllib.request, "urlopen", _fake_urlopen({"places": []})
    )
    assert serper_client.lookup_place("nonexistent place xyz") is None


def test_lookup_place_no_api_key_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert serper_client.lookup_place("anything") is None


def test_lookup_place_empty_query_returns_none() -> None:
    assert serper_client.lookup_place("   ") is None


def test_lookup_place_transport_error_raises(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-key")

    def _boom(req, timeout=None):  # noqa: ANN001
        raise serper_client.urllib.error.URLError("down")

    monkeypatch.setattr(serper_client.urllib.request, "urlopen", _boom)
    with pytest.raises(serper_client.SerperError):
        serper_client.lookup_place("anything")
