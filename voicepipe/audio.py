"""Audio device probing helpers.

This module exists to avoid hardcoding PortAudio parameters (sample rate,
channels) that may not work on a given machine.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

import numpy as np

from voicepipe.audio_device import (
    apply_pulse_source_preference,
    read_device_preference,
    resolve_device_index,
)
from voicepipe.config import device_cache_path, load_environment
logger = logging.getLogger(__name__)

_SILENCE_THRESHOLD_INT16 = 50
_SIGNAL_PROBE_SECONDS = 0.3


AudioSelectionSource = Literal[
    "config-env",
    "config-file",
    "config-pulse",
    "default",
    "cache",
    "auto",
    "fallback",
]


@dataclass(frozen=True)
class AudioInputResolution:
    selection: "AudioInputSelection"
    source: AudioSelectionSource
    device_name: str | None = None
    max_amp: int | None = None


@dataclass(frozen=True)
class AudioDeviceCacheEntry:
    device_index: int
    device_name: str
    samplerate: int
    channels: int
    source: str
    last_ok: str


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_text_file(path: Path) -> str | None:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def read_device_cache() -> AudioDeviceCacheEntry | None:
    path = device_cache_path()
    raw = _read_text_file(path)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        device_index = int(payload.get("device_index"))
        samplerate = int(payload.get("samplerate"))
        channels = int(payload.get("channels"))
    except Exception:
        return None
    device_name = payload.get("device_name")
    if not isinstance(device_name, str):
        device_name = ""
    source = payload.get("source")
    if not isinstance(source, str):
        source = "auto"
    last_ok = payload.get("last_ok")
    if not isinstance(last_ok, str):
        last_ok = ""
    return AudioDeviceCacheEntry(
        device_index=device_index,
        device_name=device_name,
        samplerate=samplerate,
        channels=channels,
        source=source,
        last_ok=last_ok,
    )


def write_device_cache(
    selection: "AudioInputSelection",
    *,
    device_name: str,
    source: str,
) -> None:
    path = device_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    payload = {
        "device_index": int(selection.device_index),
        "device_name": str(device_name or ""),
        "samplerate": int(selection.samplerate),
        "channels": int(selection.channels),
        "source": str(source or "auto"),
        "last_ok": _utc_now_iso_z(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def _device_name(device_index: int) -> str:
    _require_sounddevice()
    assert sd is not None
    try:
        info = sd.query_devices(int(device_index), "input")
    except Exception:
        try:
            info = sd.query_devices(int(device_index))
        except Exception:
            return ""
    name = info.get("name")
    return str(name) if name is not None else ""


def _probe_input_max_amp(
    *,
    device_index: int,
    samplerate: int,
    channels: int,
    seconds: float,
) -> int:
    _require_sounddevice()
    assert sd is not None
    fs = int(samplerate)
    frames = int(max(0.05, float(seconds)) * fs)
    with sd.InputStream(
        device=int(device_index),
        channels=int(channels),
        samplerate=fs,
        dtype="int16",
        blocksize=0,
    ) as stream:
        data, _overflowed = stream.read(frames)
    if not getattr(data, "size", 0):
        return 0
    # Avoid int16 abs(-32768) overflow by promoting to int32.
    arr = np.asarray(data, dtype=np.int32)
    return int(np.max(np.abs(arr))) if arr.size else 0


@dataclass(frozen=True)
class AudioDeviceProbeResult:
    selection: "AudioInputSelection"
    max_amp: int
    passes_threshold: bool


def _signal_probe_pairs(
    *,
    preferred_samplerate: int | None,
    preferred_channels: int | None,
    device_default_samplerate: float | int | None,
    max_channels: int,
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    if isinstance(preferred_samplerate, int) and isinstance(preferred_channels, int):
        pairs.append((int(preferred_samplerate), int(preferred_channels)))

    # Common/likely-good combos first.
    pairs.extend(
        [
            (48000, 2),
            (48000, 1),
            (16000, 1),
            (44100, 2),
            (44100, 1),
        ]
    )

    samplerates = _candidate_samplerates(
        preferred=preferred_samplerate,
        device_default=device_default_samplerate,
    )
    channels_list = _candidate_channels(preferred=preferred_channels, max_channels=int(max_channels))
    for ch in channels_list:
        for sr in samplerates:
            pairs.append((int(sr), int(ch)))

    # Deduplicate while preserving order and filter by max_channels.
    seen: set[tuple[int, int]] = set()
    ordered: list[tuple[int, int]] = []
    for sr, ch in pairs:
        if ch <= 0 or sr <= 0 or ch > int(max_channels):
            continue
        key = (int(sr), int(ch))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def probe_audio_signal(
    *,
    device_index: int,
    preferred_samplerate: int | None,
    preferred_channels: int | None,
    seconds: float = _SIGNAL_PROBE_SECONDS,
    silence_threshold: int = _SILENCE_THRESHOLD_INT16,
    max_successful_reads: int | None = None,
) -> AudioDeviceProbeResult | None:
    """Probe a device and return the best (device,rate,ch) and max amplitude."""
    _require_sounddevice()
    assert sd is not None

    try:
        info = sd.query_devices(int(device_index), "input")
    except Exception:
        return None

    max_channels = int(info.get("max_input_channels") or 0)
    if max_channels <= 0:
        return None

    pairs = _signal_probe_pairs(
        preferred_samplerate=preferred_samplerate,
        preferred_channels=preferred_channels,
        device_default_samplerate=info.get("default_samplerate"),
        max_channels=max_channels,
    )

    best_selection: AudioInputSelection | None = None
    best_amp: int = -1
    successful_reads = 0
    for samplerate, channels in pairs:
        try:
            max_amp = _probe_input_max_amp(
                device_index=int(device_index),
                samplerate=int(samplerate),
                channels=int(channels),
                seconds=float(seconds),
            )
            successful_reads += 1
            if max_amp > best_amp:
                best_amp = int(max_amp)
                best_selection = AudioInputSelection(
                    device_index=int(device_index),
                    samplerate=int(samplerate),
                    channels=int(channels),
                )
            if int(max_amp) > int(silence_threshold):
                assert best_selection is not None
                return AudioDeviceProbeResult(
                    selection=best_selection,
                    max_amp=int(max_amp),
                    passes_threshold=True,
                )
            if max_successful_reads is not None and successful_reads >= int(max_successful_reads):
                break
        except Exception:
            continue

    if best_selection is None:
        return None
    return AudioDeviceProbeResult(
        selection=best_selection,
        max_amp=int(best_amp),
        passes_threshold=bool(int(best_amp) > int(silence_threshold)),
    )


def _require_sounddevice() -> None:
    if sd is None:  # pragma: no cover
        raise RuntimeError(
            "sounddevice is not installed; install it to record audio (e.g. `pip install sounddevice`)"
        )


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
    # Deduplicate while preserving order.
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
    _require_sounddevice()
    assert sd is not None
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
    _require_sounddevice()
    assert sd is not None

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


def _linux_candidate_device_indices(devices: list[dict]) -> list[int]:
    candidates: list[int] = []
    assert sd is not None

    try:
        default_in = sd.default.device[0]
        if default_in is not None and int(default_in) >= 0:
            candidates.append(int(default_in))
    except Exception:
        pass

    for token in ("pulse", "pipewire", "default"):
        for idx, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            name = str(device.get("name", "")).lower()
            if token in name:
                candidates.append(int(idx))

    for idx, device in enumerate(devices):
        if device.get("max_input_channels", 0) <= 0:
            continue
        candidates.append(int(idx))

    seen: set[int] = set()
    ordered: list[int] = []
    for idx in candidates:
        if idx in seen:
            continue
        seen.add(idx)
        ordered.append(idx)
    return ordered


def resolve_audio_input(
    *,
    preferred_samplerate: int | None = None,
    preferred_channels: int | None = None,
    probe_seconds: float = _SIGNAL_PROBE_SECONDS,
    silence_threshold: int = _SILENCE_THRESHOLD_INT16,
) -> AudioInputResolution:
    """Resolve the audio input selection using config -> cache -> autodetect."""
    load_environment()
    _require_sounddevice()
    assert sd is not None

    preferred_pulse = apply_pulse_source_preference()
    env_device_raw = (os.environ.get("VOICEPIPE_DEVICE") or "").strip()
    device_pref = read_device_preference()
    if device_pref is not None:
        device_index, device_err = resolve_device_index(device_pref)
        if device_err:
            raise RuntimeError(device_err)
        if device_index is None:
            raise RuntimeError("Configured device did not resolve to an input index")
        selection = select_audio_input(
            preferred_device_index=int(device_index),
            preferred_samplerate=preferred_samplerate,
            preferred_channels=preferred_channels,
            strict_device_index=True,
        )
        return AudioInputResolution(
            selection=selection,
            source="config-env" if env_device_raw else "config-file",
            device_name=_device_name(selection.device_index),
        )

    if preferred_pulse:
        device_index, device_err = resolve_device_index(f"pulse:{preferred_pulse}")
        if device_err is None and device_index is not None:
            selection = select_audio_input(
                preferred_device_index=int(device_index),
                preferred_samplerate=preferred_samplerate,
                preferred_channels=preferred_channels,
                strict_device_index=True,
            )
            return AudioInputResolution(
                selection=selection,
                source="config-pulse",
                device_name=_device_name(selection.device_index),
            )

    # Default behavior (Linux): prefer the Pulse/PipeWire input so we track the
    # system default microphone. This intentionally does *not* require a loud
    # signal at selection time (the user may not be speaking yet).
    for spec in ("pulse", "pipewire"):
        device_index, device_err = resolve_device_index(spec)
        if device_err is not None or device_index is None:
            continue
        try:
            selection = select_audio_input(
                preferred_device_index=int(device_index),
                preferred_samplerate=preferred_samplerate,
                preferred_channels=preferred_channels,
                strict_device_index=True,
            )
            return AudioInputResolution(
                selection=selection,
                source="default",
                device_name=_device_name(selection.device_index),
            )
        except Exception:
            continue

    cached = read_device_cache()
    if cached is not None:
        probed = probe_audio_signal(
            device_index=int(cached.device_index),
            preferred_samplerate=int(cached.samplerate),
            preferred_channels=int(cached.channels),
            seconds=float(probe_seconds),
            silence_threshold=int(silence_threshold),
            max_successful_reads=None,
        )
        if probed is not None and probed.passes_threshold:
            name = _device_name(probed.selection.device_index)
            write_device_cache(probed.selection, device_name=name, source=cached.source)
            return AudioInputResolution(
                selection=probed.selection,
                source="cache",
                device_name=name,
                max_amp=int(probed.max_amp),
            )

    devices = sd.query_devices()
    # Prefer "pulse"/"pipewire"/"default" candidates first when present, but
    # fall back to probing any input-capable device. Despite the name, this
    # heuristic is useful cross-platform (it is exercised in unit tests).
    candidates = _linux_candidate_device_indices(devices)

    best: AudioDeviceProbeResult | None = None
    for idx in candidates:
        probed = probe_audio_signal(
            device_index=int(idx),
            preferred_samplerate=preferred_samplerate,
            preferred_channels=preferred_channels,
            seconds=float(probe_seconds),
            silence_threshold=int(silence_threshold),
            max_successful_reads=2,
        )
        if probed is None:
            continue

        if best is None or int(probed.max_amp) > int(best.max_amp):
            best = probed

        if probed.passes_threshold:
            name = _device_name(probed.selection.device_index)
            write_device_cache(probed.selection, device_name=name, source="auto")
            return AudioInputResolution(
                selection=probed.selection,
                source="auto",
                device_name=name,
                max_amp=int(probed.max_amp),
            )

    if best is not None:
        name = _device_name(best.selection.device_index)
        logger.warning(
            "Audio auto-detect found no loud devices (max_amp=%s); using device %s (%s). "
            "Run `voicepipe config audio` to choose a device.",
            best.max_amp,
            best.selection.device_index,
            name,
        )
        return AudioInputResolution(
            selection=best.selection,
            source="fallback",
            device_name=name,
            max_amp=int(best.max_amp),
        )

    selection = select_audio_input(
        preferred_samplerate=preferred_samplerate,
        preferred_channels=preferred_channels,
    )
    name = _device_name(selection.device_index)
    logger.warning(
        "Audio auto-detect could not probe any devices; using device %s (%s). "
        "Run `voicepipe config audio` to choose a device.",
        selection.device_index,
        name,
    )
    return AudioInputResolution(
        selection=selection,
        source="fallback",
        device_name=name,
    )


def resolve_audio_input_for_recording(
    *,
    preferred_samplerate: int | None = None,
    preferred_channels: int | None = None,
) -> AudioInputResolution:
    """Resolve an audio input selection quickly for recording.

    Unlike `resolve_audio_input`, this avoids the (slow) signal probe used for
    auto-detect loudness. It prefers:
      - explicit device config (VOICEPIPE_DEVICE / config file)
      - cached device selection (device_cache.json), verified with a quick stream probe
      - otherwise `select_audio_input`
    """
    load_environment()
    _require_sounddevice()
    assert sd is not None

    preferred_pulse = apply_pulse_source_preference()

    env_device_raw = (os.environ.get("VOICEPIPE_DEVICE") or "").strip()
    device_pref = read_device_preference()
    if device_pref is not None:
        device_index, device_err = resolve_device_index(device_pref)
        if device_err:
            raise RuntimeError(device_err)
        if device_index is None:
            raise RuntimeError("Configured device did not resolve to an input index")

        samplerate = int(preferred_samplerate) if isinstance(preferred_samplerate, int) else 16000
        channels = int(preferred_channels) if isinstance(preferred_channels, int) else 1
        return AudioInputResolution(
            selection=AudioInputSelection(
                device_index=int(device_index),
                samplerate=samplerate,
                channels=channels,
            ),
            source="config-env" if env_device_raw else "config-file",
            device_name=None,
        )

    if preferred_pulse:
        device_index, device_err = resolve_device_index(f"pulse:{preferred_pulse}")
        if device_err is None and device_index is not None:
            samplerate = int(preferred_samplerate) if isinstance(preferred_samplerate, int) else 16000
            channels = int(preferred_channels) if isinstance(preferred_channels, int) else 1
            return AudioInputResolution(
                selection=AudioInputSelection(
                    device_index=int(device_index),
                    samplerate=samplerate,
                    channels=channels,
                ),
                source="config-pulse",
                device_name=None,
            )

    # Default behavior (Linux): prefer the Pulse/PipeWire input so we track the
    # system default microphone. Keep the preferred samplerate/channels for
    # performance when possible.
    for spec in ("pulse", "pipewire"):
        device_index, device_err = resolve_device_index(spec)
        if device_err is not None or device_index is None:
            continue
        samplerate = int(preferred_samplerate) if isinstance(preferred_samplerate, int) else 16000
        channels = int(preferred_channels) if isinstance(preferred_channels, int) else 1
        return AudioInputResolution(
            selection=AudioInputSelection(
                device_index=int(device_index),
                samplerate=samplerate,
                channels=channels,
            ),
            source="default",
            device_name=None,
        )

    cached = read_device_cache()
    if cached is not None:
        return AudioInputResolution(
            selection=AudioInputSelection(
                device_index=int(cached.device_index),
                samplerate=int(cached.samplerate),
                channels=int(cached.channels),
            ),
            source="cache",
            device_name=str(getattr(cached, "device_name", "") or "") or None,
        )

    selection = select_audio_input(
        preferred_samplerate=preferred_samplerate,
        preferred_channels=preferred_channels,
    )
    return AudioInputResolution(
        selection=selection,
        source="fallback",
        device_name=None,
    )
