"""Core recording functionality for voicepipe."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import subprocess
import signal
import threading
import time
import wave
from typing import Literal

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

import numpy as np


logger = logging.getLogger(__name__)

RecordBackend = Literal["auto", "sounddevice", "arecord"]

_BACKEND_CACHE: dict[tuple[int | None, int, int], RecordBackend] = {}


def _require_sounddevice() -> None:
    if sd is None:
        raise RuntimeError(
            "sounddevice is not installed; install it to record audio (e.g. `pip install sounddevice`)"
        )


def _wait_for_process(proc: subprocess.Popen, timeout: float) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


class AudioRecorder:
    """Audio recorder supporting WAV (in-memory) and MP3 (ffmpeg) output."""

    def __init__(
        self,
        device_index: int | None = None,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        use_mp3: bool = False,
        max_duration: int | float | None = 300,
        pre_open: bool = False,
        ffmpeg_async: bool = False,
        backend: str | None = None,
    ):
        self.device_index = device_index
        self.channels = int(channels)
        self.rate = int(sample_rate)
        self.use_mp3 = bool(use_mp3)
        self.max_duration = max_duration
        self.pre_open = bool(pre_open)
        self.ffmpeg_async = bool(ffmpeg_async)

        self.backend: RecordBackend = self._resolve_backend(backend)

        self.stream = None
        self.audio_queue: queue.Queue[bytes] = queue.Queue()
        self.recording = False

        self.format = np.int16

        self.ffmpeg_process: subprocess.Popen | None = None
        self._ffmpeg_start_thread: threading.Thread | None = None
        self.timeout_timer: threading.Timer | None = None

        self._arecord_process: subprocess.Popen | None = None
        self._arecord_reader_thread: threading.Thread | None = None

        if self.pre_open and self.backend == "sounddevice":
            self._pre_open_stream()

    def _resolve_backend(self, backend: str | None) -> RecordBackend:
        requested = (backend or os.environ.get("VOICEPIPE_RECORD_BACKEND") or "auto").strip().lower()
        aliases = {
            "": "auto",
            "sd": "sounddevice",
            "portaudio": "sounddevice",
            "pa": "sounddevice",
            "alsa": "arecord",
        }
        requested = aliases.get(requested, requested)
        if requested not in ("auto", "sounddevice", "arecord"):
            requested = "auto"

        if requested != "auto":
            return requested  # type: ignore[return-value]

        # Auto mode: prefer sounddevice for lowest latency, but fall back to
        # `arecord` on Linux when PortAudio/sounddevice capture appears broken.
        if not self._can_use_arecord_fallback():
            return "sounddevice"

        key = (self.device_index, int(self.rate), int(self.channels))
        cached = _BACKEND_CACHE.get(key)
        if cached is not None:
            return cached

        chosen: RecordBackend = "sounddevice"
        try:
            if self._sounddevice_capture_seems_broken():
                chosen = "arecord"
        except Exception:
            # If the probe itself fails (device unavailable, PortAudio errors),
            # try arecord instead of permanently breaking recording.
            chosen = "arecord"

        _BACKEND_CACHE[key] = chosen
        return chosen

    def _can_use_arecord_fallback(self) -> bool:
        # Only implemented on Linux and only if `arecord` is available.
        try:
            import sys

            if not sys.platform.startswith("linux"):
                return False
        except Exception:
            return False
        return shutil.which("arecord") is not None

    def _is_pulse_like_device(self, device_index: int | None) -> bool:
        if sd is None or device_index is None:
            return False
        try:
            info = sd.query_devices(int(device_index))
        except Exception:
            return False
        name = str(info.get("name", "")).lower()
        return ("pulse" in name) or ("pipewire" in name) or (" default" in name)

    def _sounddevice_capture_seems_broken(self) -> bool:
        """Return True if PortAudio returns all-zero samples (Linux/Pulse-like)."""
        _require_sounddevice()
        assert sd is not None

        device = self.device_index
        if device is None:
            try:
                default_in = sd.default.device[0]
                if default_in is not None and int(default_in) >= 0:
                    device = int(default_in)
            except Exception:
                device = None

        # Only probe Pulse/PipeWire-ish devices; hardware devices can be
        # legitimately silent (or busy) and we don't want to force arecord.
        if not self._is_pulse_like_device(device):
            return False

        frames = int(max(256, min(4096, int(self.rate // 10))))  # ~0.1s at 48k
        with sd.InputStream(
            device=device,
            channels=int(self.channels),
            samplerate=int(self.rate),
            dtype="int16",
            blocksize=0,
        ) as stream:
            data, _overflowed = stream.read(frames)

        if not getattr(data, "size", 0):
            return True
        arr = np.asarray(data, dtype=np.int32)
        return bool(arr.size and int(np.max(np.abs(arr))) == 0)

    def _pre_open_stream(self) -> None:
        _require_sounddevice()
        try:
            self.stream = sd.InputStream(
                device=self.device_index,
                channels=self.channels,
                samplerate=self.rate,
                dtype=self.format,
                callback=self._audio_callback,
                blocksize=1024,
            )
        except Exception as e:
            logger.warning("Could not pre-open stream: %s", e)
            self.stream = None

    def get_default_device(self) -> int | None:
        """Return the default input device index (or None)."""
        _require_sounddevice()
        try:
            default_in = sd.default.device[0]
            if default_in is None:
                return None
            return int(default_in)
        except Exception:
            return None

    def _start_ffmpeg(self, output_file: str) -> subprocess.Popen:
        return subprocess.Popen(
            [
                "ffmpeg",
                "-f",
                "s16le",
                "-ar",
                str(self.rate),
                "-ac",
                str(self.channels),
                "-i",
                "-",
                "-acodec",
                "mp3",
                "-b:a",
                "64k",
                "-y",
                output_file,
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def _start_ffmpeg_async(self, output_file: str) -> None:
        def _run() -> None:
            try:
                self.ffmpeg_process = self._start_ffmpeg(output_file)
            except Exception:
                # If ffmpeg can't start, keep recording in WAV mode (queue).
                self.ffmpeg_process = None

        thread = threading.Thread(target=_run, daemon=True)
        self._ffmpeg_start_thread = thread
        thread.start()

    def _is_device_unavailable(self, error: Exception) -> bool:
        msg = str(error).lower()
        return (
            "device unavailable" in msg
            or "paerrorcode -9985" in msg
            or "device busy" in msg
            or "device is busy" in msg
        )

    def _open_stream_with_retry(self, device: int | None) -> None:
        _require_sounddevice()

        last_error: Exception | None = None
        backoff = 0.1
        for attempt in range(4):
            if self.stream:
                try:
                    self.stream.start()
                    return
                except Exception as e:
                    last_error = e
                    try:
                        self.stream.close()
                    except Exception:
                        pass
                    self.stream = None

            try:
                self.stream = sd.InputStream(
                    device=device,
                    channels=self.channels,
                    samplerate=self.rate,
                    dtype=self.format,
                    callback=self._audio_callback,
                    blocksize=1024,
                )
                self.stream.start()
                return
            except Exception as e:
                last_error = e
                if not self._is_device_unavailable(e):
                    break
                try:
                    import time as _time
                    _time.sleep(backoff)
                except Exception:
                    pass
                backoff = min(backoff * 2, 1.0)
                self.stream = None

        if last_error is not None:
            raise last_error

    def _start_arecord(self, *, pcm: str) -> None:
        arecord = shutil.which("arecord")
        if not arecord:
            raise RuntimeError("arecord not found in PATH")

        # Capture raw signed 16-bit little-endian PCM to stdout.
        argv = [
            arecord,
            "-q",
            "-D",
            str(pcm),
            "-f",
            "S16_LE",
            "-r",
            str(int(self.rate)),
            "-c",
            str(int(self.channels)),
            "-t",
            "raw",
        ]
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # If the device can't be opened, arecord will exit quickly.
        try:
            time.sleep(0.05)
        except Exception:
            pass
        if proc.poll() is not None:
            try:
                err = (proc.stderr.read() if proc.stderr else b"") if proc.stderr else b""
            except Exception:
                err = b""
            msg = err.decode(errors="replace").strip() if err else f"exit={proc.returncode}"
            raise RuntimeError(f"arecord failed to start ({pcm}): {msg}")

        self._arecord_process = proc

        def _reader() -> None:
            out = proc.stdout
            if out is None:
                return
            while True:
                try:
                    chunk = out.read(4096)
                except Exception:
                    break
                if not chunk:
                    break

                ff = self.ffmpeg_process
                if ff and ff.stdin:
                    try:
                        ff.stdin.write(chunk)
                        continue
                    except Exception:
                        pass
                try:
                    self.audio_queue.put(chunk)
                except Exception:
                    break

        thread = threading.Thread(target=_reader, daemon=True)
        self._arecord_reader_thread = thread
        thread.start()

    def _stop_arecord(self) -> None:
        proc = self._arecord_process
        if not proc:
            return

        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass

            if not _wait_for_process(proc, 2.0):
                try:
                    proc.terminate()
                except Exception:
                    pass
            if not _wait_for_process(proc, 1.0):
                try:
                    proc.kill()
                except Exception:
                    pass
                _wait_for_process(proc, 1.0)

        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            if proc.stderr:
                proc.stderr.close()
        except Exception:
            pass

        thread = self._arecord_reader_thread
        if thread:
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass
        self._arecord_reader_thread = None
        self._arecord_process = None

    def start_recording(self, output_file: str | None = None) -> None:
        """Start recording. In MP3 mode, `output_file` is required."""
        if self.backend == "sounddevice":
            _require_sounddevice()
        elif self.backend == "arecord":
            if not self._can_use_arecord_fallback():
                raise RuntimeError("arecord backend is only supported on Linux")
        else:
            # Should never happen: backend normalized in __init__.
            _require_sounddevice()

        device = self.device_index
        if self.backend == "sounddevice":
            if device is None:
                device = self.get_default_device()
        else:
            # For arecord, we ignore PortAudio indices and rely on Pulse/PipeWire
            # defaults (optionally influenced by PULSE_SOURCE).
            device = None

        try:
            if self.use_mp3 and output_file:
                if self.ffmpeg_async:
                    self._start_ffmpeg_async(output_file)
                else:
                    self.ffmpeg_process = self._start_ffmpeg(output_file)

            self.recording = True

            if self.backend == "arecord":
                # Prefer pulse to track the system default source. If it fails,
                # fall back to pipewire which may be present on some distros.
                pcm_candidates = ["pulse", "pipewire"]
                if sd is not None and self.device_index is not None:
                    try:
                        name = str(sd.query_devices(int(self.device_index)).get("name", "")).lower()
                        if "pipewire" in name:
                            pcm_candidates = ["pipewire", "pulse"]
                    except Exception:
                        pass
                last_err: Exception | None = None
                for pcm in pcm_candidates:
                    try:
                        self._start_arecord(pcm=pcm)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        continue
                if last_err is not None:
                    raise last_err
            else:
                self._open_stream_with_retry(device)

            if self.max_duration:
                self.timeout_timer = threading.Timer(
                    float(self.max_duration), self._timeout_callback
                )
                self.timeout_timer.daemon = True
                self.timeout_timer.start()

        except Exception as e:
            raise RuntimeError(f"Failed to start recording: {e}") from e

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            logger.debug("Audio callback status: %s", status)

        if not self.recording:
            return

        audio_data = indata.copy().tobytes()
        proc = self.ffmpeg_process
        if proc and proc.stdin:
            try:
                proc.stdin.write(audio_data)
                return
            except Exception:
                pass
        self.audio_queue.put(audio_data)

    def _timeout_callback(self) -> None:
        if not self.recording:
            return
        logger.info("Recording timeout reached (%ss), stopping...", self.max_duration)
        try:
            self.stop_recording()
        except Exception as e:
            logger.exception("Error during timeout handling: %s", e)
            self.cleanup()

    def _stop_ffmpeg(self) -> None:
        proc = self.ffmpeg_process
        if not proc:
            return

        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass

        if _wait_for_process(proc, 5.0):
            self.ffmpeg_process = None
            return

        try:
            proc.terminate()
        except Exception:
            pass

        if _wait_for_process(proc, 1.0):
            self.ffmpeg_process = None
            return

        try:
            proc.kill()
        except Exception:
            pass

        _wait_for_process(proc, 1.0)
        self.ffmpeg_process = None

    def stop_recording(self) -> bytes | None:
        """Stop recording and return raw PCM (WAV mode) or None (MP3 mode)."""
        if not self.recording:
            return None

        if self.timeout_timer:
            try:
                self.timeout_timer.cancel()
            except Exception:
                pass
            self.timeout_timer = None

        # Keep `self.recording` True until the backend is fully stopped so
        # in-flight producers can enqueue their final chunk(s) before we drain.
        if self.backend == "arecord":
            self._stop_arecord()
        else:
            if self.stream:
                try:
                    self.stream.stop()
                except Exception:
                    pass
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None

        self.recording = False

        if self.ffmpeg_process:
            self._stop_ffmpeg()
            return None

        frames: list[bytes] = []

        def _drain() -> bool:
            got = False
            while True:
                try:
                    frames.append(self.audio_queue.get_nowait())
                    got = True
                except queue.Empty:
                    break
                except Exception:
                    break
            return got

        _drain()
        # Best-effort: give any in-flight callback a moment to enqueue its last
        # buffer. This avoids truncation in some PortAudio/Windows environments.
        for _ in range(5):
            if not self.audio_queue.empty():
                _drain()
                continue
            try:
                time.sleep(0.01)
            except Exception:
                break
            if not _drain():
                break
        if not frames:
            raise RuntimeError("No audio data recorded")
        return b"".join(frames)

    def save_to_file(self, data: bytes, filepath: str) -> None:
        """Save recorded raw PCM (int16) to a WAV file."""
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.rate)
            wf.writeframes(data)

    def cleanup(self) -> None:
        if self.timeout_timer:
            try:
                self.timeout_timer.cancel()
            except Exception:
                pass
            self.timeout_timer = None
        if self._arecord_process:
            try:
                self._stop_arecord()
            except Exception:
                pass
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.ffmpeg_process:
            self._stop_ffmpeg()


class FastAudioRecorder(AudioRecorder):
    """Low-latency recorder (pre-opens the stream by default)."""

    def __init__(
        self,
        device_index: int | None = None,
        sample_rate: int = 16000,
        channels: int = 1,
        use_mp3: bool = False,
        max_duration: int | float | None = 300,
        pre_open: bool = True,
    ):
        super().__init__(
            device_index,
            sample_rate=sample_rate,
            channels=channels,
            use_mp3=use_mp3,
            max_duration=max_duration,
            pre_open=pre_open,
            ffmpeg_async=True,
        )
