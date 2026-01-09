"""Core recording functionality for voicepipe."""

from __future__ import annotations

import logging
import queue
import subprocess
import threading
import time
import wave

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

import numpy as np


logger = logging.getLogger(__name__)


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
    ):
        self.device_index = device_index
        self.channels = int(channels)
        self.rate = int(sample_rate)
        self.use_mp3 = bool(use_mp3)
        self.max_duration = max_duration
        self.pre_open = bool(pre_open)
        self.ffmpeg_async = bool(ffmpeg_async)

        self.stream = None
        self.audio_queue: queue.Queue[bytes] = queue.Queue()
        self.recording = False

        self.format = np.int16

        self.ffmpeg_process: subprocess.Popen | None = None
        self._ffmpeg_start_thread: threading.Thread | None = None
        self.timeout_timer: threading.Timer | None = None

        if self.pre_open:
            self._pre_open_stream()

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

    def start_recording(self, output_file: str | None = None) -> None:
        """Start recording. In MP3 mode, `output_file` is required."""
        _require_sounddevice()

        device = self.device_index
        if device is None:
            device = self.get_default_device()

        try:
            if self.use_mp3 and output_file:
                if self.ffmpeg_async:
                    self._start_ffmpeg_async(output_file)
                else:
                    self.ffmpeg_process = self._start_ffmpeg(output_file)

            self.recording = True

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

        # Keep `self.recording` True until the stream is fully stopped/closed so
        # in-flight callbacks can enqueue their final chunk(s) before we drain.
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
