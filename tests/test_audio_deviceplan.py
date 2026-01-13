from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from pathlib import Path

import numpy as np
from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.config import device_cache_path, env_file_path


class _FakeInputStream:
    def __init__(
        self,
        *,
        device: int,
        channels: int,
        samplerate: int,
        amp_by_device: dict[int, int],
        allowed_samplerates: dict[int, set[int]] | None,
        max_channels_by_device: dict[int, int],
    ) -> None:
        self._device = int(device)
        self._channels = int(channels)
        self._samplerate = int(samplerate)
        self._amp_by_device = amp_by_device
        self._allowed_samplerates = allowed_samplerates or {}
        self._max_channels_by_device = max_channels_by_device

    def __enter__(self) -> "_FakeInputStream":
        allowed = self._allowed_samplerates.get(self._device)
        if allowed and self._samplerate not in allowed:
            raise RuntimeError(f"Unsupported samplerate: {self._samplerate}")
        max_ch = int(self._max_channels_by_device.get(self._device, 0))
        if max_ch and self._channels > max_ch:
            raise RuntimeError(f"Unsupported channels: {self._channels}")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self, frames: int):
        amp = int(self._amp_by_device.get(self._device, 0))
        data = np.full((int(frames), int(self._channels)), amp, dtype=np.int16)
        return data, False


class FakeSoundDevice:
    def __init__(
        self,
        devices: list[dict],
        *,
        default_in: int,
        amp_by_device: dict[int, int],
        allowed_samplerates: dict[int, set[int]] | None = None,
    ) -> None:
        self._devices = devices
        self._amp_by_device = amp_by_device
        self._allowed_samplerates = allowed_samplerates or {}
        self.default = SimpleNamespace(device=(int(default_in), None))

        self._max_channels_by_device: dict[int, int] = {}
        for idx, d in enumerate(devices):
            self._max_channels_by_device[int(idx)] = int(d.get("max_input_channels") or 0)

    def query_devices(self, device=None, kind=None):
        if device is None:
            return list(self._devices)
        return dict(self._devices[int(device)])

    def InputStream(self, *, device, channels, samplerate, dtype, blocksize=0, callback=None):
        return _FakeInputStream(
            device=int(device),
            channels=int(channels),
            samplerate=int(samplerate),
            amp_by_device=self._amp_by_device,
            allowed_samplerates=self._allowed_samplerates,
            max_channels_by_device=self._max_channels_by_device,
        )

    def rec(self, frames, *, samplerate, channels, dtype, device):
        amp = int(self._amp_by_device.get(int(device), 0))
        return np.full((int(frames), int(channels)), amp, dtype=np.int16)

    def wait(self):
        return None


def _load_cache() -> dict:
    return json.loads(device_cache_path().read_text(encoding="utf-8"))


def test_resolve_audio_input_autodetect_uses_signal_probe_and_cache(
    isolated_home: Path, monkeypatch
) -> None:
    import voicepipe.audio as audio

    devices = [
        {"name": "pulse", "max_input_channels": 2, "default_samplerate": 48000},
        {"name": "hw:0,6", "max_input_channels": 2, "default_samplerate": 48000},
    ]
    fake = FakeSoundDevice(
        devices,
        default_in=0,
        amp_by_device={0: 0, 1: 200},
        allowed_samplerates={0: {16000, 48000}, 1: {48000}},
    )
    monkeypatch.setattr(audio, "sd", fake)
    monkeypatch.setattr(audio, "get_default_pulse_source", lambda: None)

    # First call: should reject silent pulse and choose hw:0,6, writing cache.
    res1 = audio.resolve_audio_input(
        preferred_samplerate=16000,
        preferred_channels=1,
        probe_seconds=0.05,
        silence_threshold=50,
    )
    assert res1.source == "auto"
    assert res1.selection.device_index == 1
    assert res1.selection.samplerate == 48000
    assert device_cache_path().exists()
    cache = _load_cache()
    assert cache["device_index"] == 1

    # Second call: should load cache and keep using the cached device.
    res2 = audio.resolve_audio_input(
        preferred_samplerate=16000,
        preferred_channels=1,
        probe_seconds=0.05,
        silence_threshold=50,
    )
    assert res2.source == "cache"
    assert res2.selection.device_index == 1


def test_resolve_audio_input_env_override_is_strict(isolated_home: Path, monkeypatch) -> None:
    import voicepipe.audio as audio

    devices = [
        {"name": "pulse", "max_input_channels": 2, "default_samplerate": 48000},
        {"name": "hw:0,6", "max_input_channels": 2, "default_samplerate": 48000},
    ]
    fake = FakeSoundDevice(
        devices,
        default_in=0,
        amp_by_device={0: 0, 1: 200},
        allowed_samplerates={0: {16000, 48000}, 1: {48000}},
    )
    monkeypatch.setattr(audio, "sd", fake)
    monkeypatch.setattr(audio, "get_default_pulse_source", lambda: None)
    monkeypatch.setenv("VOICEPIPE_DEVICE", "0")

    res = audio.resolve_audio_input(
        preferred_samplerate=16000,
        preferred_channels=1,
        probe_seconds=0.05,
        silence_threshold=50,
    )
    assert res.source == "config-env"
    assert res.selection.device_index == 0


def test_config_audio_wizard_writes_env_and_cache(isolated_home: Path, monkeypatch) -> None:
    import voicepipe.audio as audio
    import voicepipe.commands.config as config_cmd

    devices = [
        {"name": "pulse", "max_input_channels": 2, "default_samplerate": 48000},
        {"name": "hw:0,6", "max_input_channels": 2, "default_samplerate": 48000},
    ]
    fake = FakeSoundDevice(
        devices,
        default_in=0,
        amp_by_device={0: 0, 1: 200},
        allowed_samplerates={0: {16000, 48000}, 1: {48000}},
    )
    monkeypatch.setattr(audio, "sd", fake)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    monkeypatch.setattr(config_cmd, "list_pulse_sources", lambda: [])

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["config", "audio", "--wizard", "--seconds", "0.05"],
        input="\n",
    )
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert env_path.exists()
    assert "VOICEPIPE_DEVICE=1" in env_path.read_text(encoding="utf-8")

    cache_path = device_cache_path()
    assert cache_path.exists()
    cache = _load_cache()
    assert cache["device_index"] == 1
    assert cache["source"] == "manual"
