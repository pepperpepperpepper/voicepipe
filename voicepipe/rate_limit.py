"""Per-verb sliding-window rate limits for Zwingli verbs.

Each verb that sets ``rate_limit_per_min`` gets its own 60-second sliding
window. The N-th call within the window raises :class:`RateLimitExceeded`
with the time until the oldest call ages out, so the caller can show a
"retry in 12.3s" hint.

State is module-level and in-memory only — there's no persistence across
processes. Voicepipe is single-process in normal use; if a future
multi-process layout needs shared counters, swap this for a file-backed
implementation behind the same API.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Optional


_WINDOW_SECONDS = 60.0


class RateLimitExceeded(RuntimeError):
    """Raised when a verb's per-minute call cap would be exceeded.

    ``retry_after_seconds`` is how long until the oldest call in the
    sliding window expires (so the next call would succeed).
    """

    def __init__(
        self,
        message: str,
        *,
        verb: str,
        cap_per_min: int,
        retry_after_seconds: float,
    ) -> None:
        super().__init__(message)
        self.verb = verb
        self.cap_per_min = int(cap_per_min)
        self.retry_after_seconds = float(retry_after_seconds)


_LOCK = threading.Lock()
_HITS: dict[str, deque[float]] = defaultdict(deque)


def check_and_record(
    verb: str,
    cap_per_min: int,
    *,
    now: Optional[float] = None,
) -> None:
    """Raise :class:`RateLimitExceeded` if calling ``verb`` would exceed its cap.

    A cap of 0 (or negative) disables the limit. On a successful check the
    call is recorded in the per-verb sliding window.
    """
    cap = int(cap_per_min)
    if cap <= 0:
        return

    cleaned = (verb or "").strip().lower()
    if not cleaned:
        return

    current = float(now) if now is not None else time.monotonic()
    cutoff = current - _WINDOW_SECONDS

    with _LOCK:
        window = _HITS[cleaned]
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= cap:
            oldest = window[0]
            retry_after = max(0.0, (oldest + _WINDOW_SECONDS) - current)
            raise RateLimitExceeded(
                f"verb {cleaned!r} rate limit exceeded ({cap}/min); "
                f"retry in {retry_after:.1f}s",
                verb=cleaned,
                cap_per_min=cap,
                retry_after_seconds=retry_after,
            )

        window.append(current)


def reset_for_tests() -> None:
    """Clear all per-verb windows. Test-only helper."""
    with _LOCK:
        _HITS.clear()


def snapshot() -> dict[str, int]:
    """Return a debug snapshot of current in-window call counts per verb."""
    with _LOCK:
        return {verb: len(window) for verb, window in _HITS.items() if window}
