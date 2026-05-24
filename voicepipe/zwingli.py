from __future__ import annotations

import threading
import time
import os
from collections import deque
from typing import Optional

from voicepipe.config import (
    get_groq_api_key,
    get_openai_api_key,
    get_zwingli_backend,
    get_zwingli_base_url,
    get_zwingli_model,
    get_zwingli_system_prompt,
    get_zwingli_temperature,
    get_zwingli_user_prompt,
)


_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_HITS: deque[float] = deque()
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMIT_DEFAULT_PER_MIN = 10


class ZwingliRateLimitError(RuntimeError):
    """Raised when too many Zwingli LLM calls happen within the rate-limit window."""

    def __init__(self, message: str, *, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = float(retry_after_seconds)


def _rate_limit_per_minute() -> int:
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN") or "").strip()
    if not raw:
        return _RATE_LIMIT_DEFAULT_PER_MIN
    try:
        value = int(raw)
    except ValueError:
        return _RATE_LIMIT_DEFAULT_PER_MIN
    if value < 0:
        return _RATE_LIMIT_DEFAULT_PER_MIN
    return value


def _check_rate_limit(now: Optional[float] = None) -> None:
    """Raise ZwingliRateLimitError if the current call would exceed the per-minute cap.

    A cap of 0 disables the limit entirely (matches "off"). Otherwise this is a
    sliding-window counter: we drop hits older than the window and reject the
    call if the window already holds `cap` hits.
    """
    cap = _rate_limit_per_minute()
    if cap <= 0:
        return

    current = float(now) if now is not None else time.monotonic()
    cutoff = current - _RATE_LIMIT_WINDOW_SECONDS

    with _RATE_LIMIT_LOCK:
        while _RATE_LIMIT_HITS and _RATE_LIMIT_HITS[0] < cutoff:
            _RATE_LIMIT_HITS.popleft()

        if len(_RATE_LIMIT_HITS) >= cap:
            oldest = _RATE_LIMIT_HITS[0]
            retry_after = max(0.0, (oldest + _RATE_LIMIT_WINDOW_SECONDS) - current)
            raise ZwingliRateLimitError(
                f"Zwingli rate-limit exceeded ({cap}/min); retry in "
                f"{retry_after:.1f}s. Set VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN "
                "to raise or disable (0).",
                retry_after_seconds=retry_after,
            )

        _RATE_LIMIT_HITS.append(current)


def _reset_rate_limit_state_for_tests() -> None:
    """Test helper: clear the sliding window so tests don't contaminate each other."""
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_HITS.clear()

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = e
else:
    _OPENAI_IMPORT_ERROR = None


def _safe_model_dump(value) -> dict | None:
    if value is None:
        return None
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump()  # type: ignore[no-any-return]
    except Exception:
        pass
    try:
        if hasattr(value, "to_dict"):
            return value.to_dict()  # type: ignore[no-any-return]
    except Exception:
        pass
    if isinstance(value, dict):
        return value
    return None


def process_zwingli_prompt_result(
    prompt: str,
    *,
    backend: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    user_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> tuple[str, dict]:
    """Process a spoken "zwingli" prompt and return (text, metadata)."""

    cleaned = (prompt or "").strip()
    if not cleaned:
        return "", {"backend": backend or get_zwingli_backend(), "model": model or ""}

    if OpenAI is None:
        raise RuntimeError(
            "openai is not installed; install it to use zwingli processing "
            "(e.g. `pip install openai`)"
        ) from _OPENAI_IMPORT_ERROR

    resolved_backend = (backend or "").strip() or get_zwingli_backend()
    resolved_model = (model or "").strip() or get_zwingli_model()
    resolved_temperature = float(get_zwingli_temperature() if temperature is None else temperature)
    resolved_user_prompt = user_prompt if user_prompt is not None else get_zwingli_user_prompt()
    resolved_system_prompt = system_prompt if system_prompt is not None else get_zwingli_system_prompt()
    resolved_user_prompt = (resolved_user_prompt or "").strip()

    resolved_base_url = base_url
    if resolved_base_url is None:
        resolved_base_url = get_zwingli_base_url()

    if not api_key:
        env_override = (os.environ.get("VOICEPIPE_ZWINGLI_API_KEY") or "").strip()
        if env_override:
            api_key = env_override

    if not api_key:
        if resolved_backend == "groq":
            api_key = get_groq_api_key()
        elif resolved_backend == "openai":
            api_key = get_openai_api_key()
        else:
            raise RuntimeError(
                "Unsupported zwingli backend.\n\n"
                "Set VOICEPIPE_ZWINGLI_BACKEND (or [zwingli].backend) to one of: groq, openai\n"
                f"Got backend={resolved_backend!r}"
            )

    messages = [{"role": "system", "content": resolved_system_prompt}]
    if resolved_user_prompt:
        messages.append({"role": "user", "content": resolved_user_prompt})
    messages.append({"role": "user", "content": cleaned})

    _check_rate_limit()

    started = time.monotonic()
    client_kwargs: dict[str, str] = {"api_key": api_key}
    if resolved_base_url:
        client_kwargs["base_url"] = resolved_base_url
    client = OpenAI(**client_kwargs)

    response = client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        temperature=resolved_temperature,
    )
    duration_ms = int((time.monotonic() - started) * 1000)

    content = ""
    finish_reason = None
    try:
        choice0 = response.choices[0]
        finish_reason = getattr(choice0, "finish_reason", None)
        content = choice0.message.content or ""
    except Exception:
        content = ""

    content = content.strip()
    if not content:
        raise RuntimeError("Zwingli model returned empty output")

    meta: dict = {
        "backend": resolved_backend,
        "model": resolved_model,
        "temperature": resolved_temperature,
        "duration_ms": duration_ms,
    }
    provider: dict = {}
    if resolved_base_url:
        provider["base_url"] = resolved_base_url
    try:
        if getattr(response, "id", None):
            provider["request_id"] = response.id
    except Exception:
        pass

    try:
        usage = _safe_model_dump(getattr(response, "usage", None))
        if usage:
            provider["usage"] = usage
    except Exception:
        pass

    if provider:
        meta["provider"] = provider
    if finish_reason:
        meta["finish_reason"] = finish_reason

    return content, meta


def process_zwingli_prompt(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    backend: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    user_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    """Process a spoken "zwingli" prompt with an LLM and return text to output/type."""
    text, _meta = process_zwingli_prompt_result(
        prompt,
        backend=backend,
        api_key=api_key,
        base_url=base_url,
        model=model,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    return text
