from __future__ import annotations

import time
from typing import Optional

from voicepipe.config import (
    get_openai_api_key,
    get_zwingli_api_key,
    get_zwingli_base_url,
    get_zwingli_model,
    get_zwingli_system_prompt,
    get_zwingli_temperature,
    get_zwingli_user_prompt,
)

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = e
else:
    _OPENAI_IMPORT_ERROR = None


_CLIENT_CACHE: dict[tuple[str, str], object] = {}


def _openai_client(*, api_key: str, base_url: str) -> object:
    key = (api_key, base_url or "")
    cached = _CLIENT_CACHE.get(key)
    if cached is not None:
        return cached
    if OpenAI is None:
        raise RuntimeError(
            "openai is not installed; install it to use zwingli processing "
            "(e.g. `pip install openai`)"
        ) from _OPENAI_IMPORT_ERROR
    kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)
    _CLIENT_CACHE[key] = client
    return client


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
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    user_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> tuple[str, dict]:
    """Process a transcript trigger prompt and return (text, metadata)."""

    cleaned = (prompt or "").strip()
    if not cleaned:
        return "", {"model": (model or "").strip()}

    resolved_model = (model or "").strip() or get_zwingli_model()
    resolved_temperature = float(get_zwingli_temperature() if temperature is None else temperature)
    resolved_system_prompt = (
        system_prompt if system_prompt is not None else get_zwingli_system_prompt()
    )
    resolved_user_prompt = user_prompt if user_prompt is not None else get_zwingli_user_prompt()
    resolved_user_prompt = (resolved_user_prompt or "").strip()

    resolved_base_url = base_url
    if resolved_base_url is None:
        resolved_base_url = get_zwingli_base_url()

    resolved_api_key = (api_key or "").strip()
    if not resolved_api_key:
        resolved_api_key = get_zwingli_api_key()
    if not resolved_api_key:
        # Fall back to the standard OpenAI key for convenience.
        resolved_api_key = get_openai_api_key()

    messages = [{"role": "system", "content": resolved_system_prompt}]
    if resolved_user_prompt:
        messages.append({"role": "user", "content": resolved_user_prompt})
    messages.append({"role": "user", "content": cleaned})

    started = time.monotonic()
    client = _openai_client(api_key=resolved_api_key, base_url=(resolved_base_url or ""))
    response = client.chat.completions.create(  # type: ignore[attr-defined]
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
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    user_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    """Process a transcript trigger prompt with an LLM and return output text."""
    text, _meta = process_zwingli_prompt_result(
        prompt,
        api_key=api_key,
        base_url=base_url,
        model=model,
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        temperature=temperature,
    )
    return text

