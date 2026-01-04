"""Helpers for selecting audio input devices."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union, Tuple

_DEVICE_CONFIG_PATHS = (
    Path.home() / ".config" / "voicepipe" / "device",
    Path.home() / ".voicepipe_device",
)
_PULSE_SOURCE_PATHS = (
    Path.home() / ".config" / "voicepipe" / "pulse_source",
    Path.home() / ".voicepipe_pulse_source",
)


def read_device_preference() -> Optional[str]:
    """Return preferred device spec from env or config file."""
    env = os.environ.get("VOICEPIPE_DEVICE")
    if env:
        value = env.strip()
        if value:
            return value

    for path in _DEVICE_CONFIG_PATHS:
        try:
            if path.exists():
                value = path.read_text().strip()
                if value:
                    return value
        except Exception:
            continue

    return None


def parse_device_spec(spec: Optional[object]) -> Optional[Union[int, str]]:
    """Parse device spec; allow int index or string name."""
    if spec is None:
        return None
    if isinstance(spec, int):
        return spec
    if isinstance(spec, str):
        value = spec.strip()
        if not value:
            return None
        if value.isdigit():
            return int(value)
        return value
    return None


def match_device_by_name(name: str) -> Optional[int]:
    """Return the first input device index matching the provided name."""
    try:
        import sounddevice as sd
    except Exception:
        return None

    try:
        devices = sd.query_devices()
    except Exception:
        return None

    needle = name.lower()
    exact = []
    partial = []
    for device_index, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        dev_name = str(device.get("name", ""))
        dev_lower = dev_name.lower()
        if dev_lower == needle:
            exact.append(device_index)
        elif needle in dev_lower:
            partial.append(device_index)

    if exact:
        return exact[0]
    if partial:
        return partial[0]
    return None


def read_pulse_source_preference() -> Optional[str]:
    """Return preferred PulseAudio source from env or config."""
    env = os.environ.get("VOICEPIPE_PULSE_SOURCE")
    if env:
        value = env.strip()
        if value:
            return value

    for path in _PULSE_SOURCE_PATHS:
        try:
            if path.exists():
                value = path.read_text().strip()
                if value:
                    return value
        except Exception:
            continue

    env = os.environ.get("PULSE_SOURCE")
    if env:
        value = env.strip()
        if value:
            return value

    return None


def apply_pulse_source_preference() -> Optional[str]:
    """Apply PulseAudio source preference to the environment."""
    preferred = read_pulse_source_preference()
    if preferred:
        os.environ["PULSE_SOURCE"] = preferred
        return preferred
    return None


def resolve_device_index(spec: Optional[object]) -> Tuple[Optional[int], Optional[str]]:
    """Resolve a device spec to an input device index when possible."""
    parsed = parse_device_spec(spec)
    if parsed is None:
        return None, None
    if isinstance(parsed, str):
        lower = parsed.lower()
        if lower.startswith("pulse:"):
            source = parsed.split(":", 1)[1].strip()
            if source:
                os.environ["PULSE_SOURCE"] = source
            idx = match_device_by_name("pulse")
            if idx is None:
                return None, "pulse device not found"
            return idx, None
        if lower == "pulse":
            idx = match_device_by_name("pulse")
            if idx is None:
                return None, "pulse device not found"
            return idx, None
        idx = match_device_by_name(parsed)
        if idx is None:
            return None, f"device not found: {parsed}"
        return idx, None
    return parsed, None
