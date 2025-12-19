"""Device parsing and validation helpers."""

from __future__ import annotations

from typing import Any, Optional, Tuple


def parse_device_index(device: Any) -> Tuple[Optional[int], Optional[str]]:
    """Coerce an incoming device value into an int or None.

    Returns:
      (device_index, error_message)
    """
    if device is None or device == "":
        return None, None
    if isinstance(device, bool):
        return None, "device must be an integer"
    if isinstance(device, int):
        return device, None
    if isinstance(device, str):
        try:
            return int(device.strip()), None
        except ValueError:
            return None, "device must be an integer"
    return None, "device must be an integer"
