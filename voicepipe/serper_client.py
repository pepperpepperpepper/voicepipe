"""Minimal Serper (serper.dev) client — server-side business/place lookup.

Used to resolve a spoken business name ("the Sukhothai Hotel in Shanghai")
into a phone number so the ``call`` verb can emit a ``dial`` action. The API
key is a server secret (``SERPER_API_KEY``); this never runs on the client.

Stdlib-only (urllib) to avoid adding a dependency to the Lambda image.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


_PLACES_ENDPOINT = "https://google.serper.dev/places"


class SerperError(RuntimeError):
    """Serper lookup failed (network, auth, or bad response)."""


def _resolve_api_key(api_key: str | None) -> str | None:
    return (api_key or os.environ.get("SERPER_API_KEY") or "").strip() or None


def lookup_place(
    query: str,
    *,
    api_key: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Return the best matching place for ``query`` or ``None``.

    The result dict carries ``name``, ``phone`` (may be empty), ``address``
    (may be empty). Returns ``None`` when there's no API key, no query, or no
    result. Raises :class:`SerperError` on a transport/HTTP failure so the
    caller can surface a distinct "lookup failed" message.
    """
    q = (query or "").strip()
    if not q:
        return None
    key = _resolve_api_key(api_key)
    if not key:
        return None

    payload = json.dumps({"q": q}).encode("utf-8")
    req = urllib.request.Request(
        _PLACES_ENDPOINT,
        data=payload,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SerperError(f"Serper lookup failed: {e}") from e

    places = data.get("places")
    if not isinstance(places, list) or not places:
        return None
    best = places[0]
    if not isinstance(best, dict):
        return None
    return {
        "name": str(best.get("title") or "").strip(),
        "phone": str(best.get("phoneNumber") or "").strip(),
        "address": str(best.get("address") or "").strip(),
    }


def lookup_phone(query: str, **kwargs: Any) -> str | None:
    """Convenience: the phone number for ``query``'s best place match, or None.

    Scans the first few results for one that actually has a phone number.
    """
    q = (query or "").strip()
    if not q:
        return None
    key = _resolve_api_key(kwargs.get("api_key"))
    if not key:
        return None
    # lookup_place returns the top hit; re-query here would be wasteful, so
    # reuse it and accept the top result's phone.
    place = lookup_place(q, **kwargs)
    if place and place.get("phone"):
        return place["phone"]
    return None
