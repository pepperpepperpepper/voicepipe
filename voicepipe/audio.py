"""Audio device probing helpers.

Main branch note:
This repo has multiple branches with different layouts. This module keeps the
recording path environment-agnostic by probing for a working (device,
samplerate, channels) tuple instead of hardcoding 16kHz mono (which can fail on
some PortAudio/ALSA setups).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import sounddevice as sd

logger = logging.getLogger(__name__)


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for v in values:
        if v <= 0 or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _candidate_samplerates(
    *,
    preferred: int | None,
    device_default: float | int | None,
) -> list[int]:
    candidates: list[int] = []
    if isinstance(preferred, int) and preferred > 0:
        candidates.append(preferred)
    candidates.append(16000)
    if isinstance(device_default, (int, float)) and device_default > 0:
        candidates.append(int(device_default))
    candidates.extend([48000, 44100, 24000, 22050])
    return _unique_ints(candidates)


def _candidate_channels(*, preferred: int | None, max_channels: int) -> list[int]:
    candidates: list[int] = []
    if isinstance(preferred, int) and preferred > 0:
        candidates.append(preferred)
    candidates.append(1)
    candidates.append(2)
    candidates = [c for c in candidates if 1 <= c <= int(max_channels)]
    seen: set[int] = set()
    ordered: list[int] = []
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        ordered.append(c)
    return ordered


@dataclass(frozen=True)
class AudioInputSelection:
    device_index: int
    samplerate: int
    channels: int


def _probe_input_stream(*, device_index: int, samplerate: int, channels: int) -> None:
    with sd.InputStream(
        device=device_index,
        channels=channels,
        samplerate=samplerate,
        dtype="int16",
        blocksize=1024,
    ) as stream:
        stream.read(1024)


def select_audio_input(
    *,
    preferred_device_index: int | None = None,
    preferred_samplerate: int | None = None,
    preferred_channels: int | None = None,
    strict_device_index: bool = False,
) -> AudioInputSelection:
    """Return a working (device, samplerate, channels) tuple.

    Preference order:
    - preferred device (if provided)
    - sounddevice default input
    - then any input-capable device, preferring names containing "default/pulse/pipewire"
    """
    devices = sd.query_devices()

    if strict_device_index:
        if not isinstance(preferred_device_index, int):
            raise ValueError("strict_device_index=True requires preferred_device_index")
        ordered = [preferred_device_index]
    else:
        candidates: list[int] = []
        if isinstance(preferred_device_index, int):
            candidates.append(preferred_device_index)

        try:
            default_in = sd.default.device[0]
            if default_in is not None and int(default_in) >= 0:
                candidates.append(int(default_in))
        except Exception:
            pass

        preferred_tokens = (" default", "pulse", "pipewire")
        for idx, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            name = str(device.get("name", "")).lower()
            if any(tok.strip() in name for tok in preferred_tokens):
                candidates.append(idx)

        for idx, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            candidates.append(idx)

        seen: set[int] = set()
        ordered: list[int] = []
        for idx in candidates:
            if idx in seen:
                continue
            seen.add(idx)
            ordered.append(idx)

    last_error: Exception | None = None
    for device_index in ordered:
        try:
            info = sd.query_devices(device_index, "input")
        except Exception as e:
            last_error = e
            continue

        max_channels = int(info.get("max_input_channels") or 0)
        if max_channels <= 0:
            continue

        samplerates = _candidate_samplerates(
            preferred=preferred_samplerate,
            device_default=info.get("default_samplerate"),
        )
        channels_list = _candidate_channels(
            preferred=preferred_channels,
            max_channels=max_channels,
        )

        for channels in channels_list:
            for samplerate in samplerates:
                try:
                    _probe_input_stream(
                        device_index=device_index,
                        samplerate=samplerate,
                        channels=channels,
                    )
                    logger.info(
                        "Selected audio input: device=%s samplerate=%s channels=%s",
                        device_index,
                        samplerate,
                        channels,
                    )
                    return AudioInputSelection(
                        device_index=device_index,
                        samplerate=samplerate,
                        channels=channels,
                    )
                except Exception as e:
                    last_error = e
                    continue

    msg = "No working audio input device found"
    if last_error is not None:
        msg = f"{msg}: {last_error}"
    raise RuntimeError(msg)

