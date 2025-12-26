from __future__ import annotations

from typing import Optional

from voicepipe.config import (
    get_openai_api_key,
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


def process_zwingli_prompt(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    user_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    """Process a spoken "zwingli" prompt with an LLM and return text to output/type."""

    cleaned = (prompt or "").strip()
    if not cleaned:
        return ""

    if OpenAI is None:
        raise RuntimeError(
            "openai is not installed; install it to use zwingli processing "
            "(e.g. `pip install openai`)"
        ) from _OPENAI_IMPORT_ERROR

    api_key = (api_key or "").strip() or get_openai_api_key()
    model = (model or "").strip() or get_zwingli_model()
    user_prompt = user_prompt if user_prompt is not None else get_zwingli_user_prompt()
    system_prompt = system_prompt if system_prompt is not None else get_zwingli_system_prompt()
    temperature = float(get_zwingli_temperature() if temperature is None else temperature)

    user_prompt = (user_prompt or "").strip()

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
    ]
    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})
    messages.append({"role": "user", "content": cleaned})

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    content = ""
    try:
        content = response.choices[0].message.content or ""
    except Exception:
        content = ""

    content = content.strip()
    if not content:
        raise RuntimeError("Zwingli model returned empty output")
    return content
