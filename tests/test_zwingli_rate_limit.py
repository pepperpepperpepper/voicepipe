from __future__ import annotations

import pytest

import voicepipe.zwingli as zwingli


@pytest.fixture(autouse=True)
def _clear_rate_limit_state() -> None:
    zwingli._reset_rate_limit_state_for_tests()
    yield
    zwingli._reset_rate_limit_state_for_tests()


def test_rate_limit_allows_up_to_cap(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN", "3")
    for i in range(3):
        zwingli._check_rate_limit(now=100.0 + i)


def test_rate_limit_rejects_past_cap_with_retry_after(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN", "2")
    zwingli._check_rate_limit(now=100.0)
    zwingli._check_rate_limit(now=101.0)

    with pytest.raises(zwingli.ZwingliRateLimitError) as exc_info:
        zwingli._check_rate_limit(now=102.0)

    err = exc_info.value
    # First hit was at 100s; window of 60s closes at 160s. Current is 102s.
    assert err.retry_after_seconds == pytest.approx(58.0, abs=0.01)
    assert "rate-limit exceeded" in str(err)


def test_rate_limit_window_slides(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN", "2")
    zwingli._check_rate_limit(now=100.0)
    zwingli._check_rate_limit(now=101.0)

    # 70s later, both hits should have aged out of the 60s window.
    zwingli._check_rate_limit(now=170.0)


def test_rate_limit_disabled_when_zero(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN", "0")
    for i in range(100):
        zwingli._check_rate_limit(now=100.0 + i * 0.001)


def test_rate_limit_invalid_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN", "not-a-number")
    # Default cap is 10; eleven calls should trip.
    for i in range(10):
        zwingli._check_rate_limit(now=100.0 + i)
    with pytest.raises(zwingli.ZwingliRateLimitError):
        zwingli._check_rate_limit(now=111.0)
