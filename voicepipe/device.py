"""Device parsing and validation helpers."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from voicepipe.audio_device import resolve_device_index


def parse_device_index(device: Any) -> Tuple[Optional[int], Optional[str]]:
    """Coerce an incoming device value into an int or None.

    Returns:
      (device_index, error_message)
    """
    if device is None or device == "":
        return None, None
    if isinstance(device, bool):
        return None, "device must be a device index or name"

    resolved, err = resolve_device_index(device)
    if err:
        return None, err
    return resolved, None
