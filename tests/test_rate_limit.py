from __future__ import annotations

import pytest

import voicepipe.rate_limit as rl


@pytest.fixture(autouse=True)
def _isolated_state() -> None:
    rl.reset_for_tests()
    yield
    rl.reset_for_tests()


def test_cap_of_zero_is_disabled() -> None:
    # Should never raise no matter how many calls.
    for _ in range(100):
        rl.check_and_record("subprocess", 0)


def test_cap_negative_is_disabled() -> None:
    for _ in range(20):
        rl.check_and_record("subprocess", -1)


def test_empty_verb_name_is_no_op() -> None:
    for _ in range(20):
        rl.check_and_record("", 1)
        rl.check_and_record("   ", 1)


def test_under_cap_passes(monkeypatch) -> None:
    now = 1000.0
    for i in range(5):
        rl.check_and_record("subprocess", 5, now=now + i)
    assert rl.snapshot() == {"subprocess": 5}


def test_at_cap_raises_with_retry_after() -> None:
    now = 1000.0
    for _ in range(3):
        rl.check_and_record("subprocess", 3, now=now)
    with pytest.raises(rl.RateLimitExceeded) as exc_info:
        rl.check_and_record("subprocess", 3, now=now + 0.5)
    assert exc_info.value.verb == "subprocess"
    assert exc_info.value.cap_per_min == 3
    # Oldest call was at `now`, window is 60s, retry after ~59.5s.
    assert 59.0 < exc_info.value.retry_after_seconds <= 60.0


def test_window_slides_forward() -> None:
    now = 1000.0
    # Fill the window.
    for _ in range(3):
        rl.check_and_record("subprocess", 3, now=now)
    # 61 seconds later, all prior hits should have aged out.
    rl.check_and_record("subprocess", 3, now=now + 61.0)
    rl.check_and_record("subprocess", 3, now=now + 61.0)
    rl.check_and_record("subprocess", 3, now=now + 61.0)
    assert rl.snapshot() == {"subprocess": 3}


def test_separate_verbs_have_separate_windows() -> None:
    now = 1000.0
    for _ in range(3):
        rl.check_and_record("subprocess", 3, now=now)
    # 'execute' has its own window; cap=3 doesn't trigger here.
    for _ in range(3):
        rl.check_and_record("execute", 3, now=now)
    # 'subprocess' is at cap; 'execute' is also at cap (but independently).
    with pytest.raises(rl.RateLimitExceeded):
        rl.check_and_record("subprocess", 3, now=now)
    with pytest.raises(rl.RateLimitExceeded):
        rl.check_and_record("execute", 3, now=now)


def test_verb_normalized_to_lowercase_and_stripped() -> None:
    now = 1000.0
    rl.check_and_record("  SubProcess  ", 1, now=now)
    with pytest.raises(rl.RateLimitExceeded) as exc_info:
        rl.check_and_record("subprocess", 1, now=now)
    assert exc_info.value.verb == "subprocess"


def test_rejected_call_does_not_consume_a_slot() -> None:
    now = 1000.0
    rl.check_and_record("subprocess", 2, now=now)
    rl.check_and_record("subprocess", 2, now=now)
    # Third call rejected
    with pytest.raises(rl.RateLimitExceeded):
        rl.check_and_record("subprocess", 2, now=now)
    # After the oldest ages out, we should accept a new call (only 2 in window).
    rl.check_and_record("subprocess", 2, now=now + 61.0)
    assert rl.snapshot()["subprocess"] == 1
