"""Daemon service for voicepipe recording."""

import os
import sys
import signal
import socket
import json
import logging
import threading
import tempfile
import time
import uuid
from pathlib import Path

try:
    import sounddevice as sd
except Exception:  # pragma: no cover
    sd = None  # type: ignore[assignment]

from .audio import resolve_audio_input, select_audio_input
from .audio_device import apply_pulse_source_preference, get_default_pulse_source
from .config import get_audio_channels, get_audio_sample_rate
from .device import parse_device_index
from .logging_utils import configure_logging
from .paths import audio_tmp_dir, daemon_socket_path
from .recorder import FastAudioRecorder
from .systray import get_systray

logger = logging.getLogger(__name__)

class RecordingDaemon:
    """Background daemon that handles recording requests."""

    def __init__(self):
        self._state_lock = threading.Lock()
        self.socket_path = daemon_socket_path()
        self.socket = None
        self.recorder = None
        self.recording = False
        self.audio_file = None
        self.recording_id = None
        self.running = True
        self.timeout_timer = None
        self.default_device = None
        self.default_samplerate = None
        self.default_channels = None
        self._timeout_triggered = False
        self._pulse_default_source = None
        self._initialize_audio()

    def _is_pulse_like_device(self, device_index: int | None) -> bool:
        if sd is None:
            return False
        if device_index is None:
            return False
        try:
            info = sd.query_devices(int(device_index))
        except Exception:
            return False
        name = str(info.get("name", "")).lower()
        return ("pulse" in name) or ("pipewire" in name)

    def _update_pulse_default_source_snapshot(self) -> None:
        """Track the Pulse default source at the time we open a Pulse-like stream.

        This lets us recreate the pre-opened PortAudio stream when the system default
        source changes, without requiring a daemon restart.
        """
        self._pulse_default_source = None
        if self.recorder is None:
            return
        if os.environ.get("PULSE_SOURCE"):
            return
        if not self._is_pulse_like_device(getattr(self.recorder, "device_index", None)):
            return
        self._pulse_default_source = get_default_pulse_source()

    def _refresh_pulse_stream_if_default_changed(self) -> None:
        if self.recorder is None:
            return
        if os.environ.get("PULSE_SOURCE"):
            # Explicit source pinning: don't follow the system default.
            return
        device_index = getattr(self.recorder, "device_index", None)
        if not self._is_pulse_like_device(device_index):
            return
        current = get_default_pulse_source()
        if not current:
            return
        previous = self._pulse_default_source
        if previous is None:
            self._pulse_default_source = current
            return
        if current == previous:
            return

        try:
            rate = int(getattr(self.recorder, "rate", 16000))
            channels = int(getattr(self.recorder, "channels", 1))
            use_mp3 = bool(getattr(self.recorder, "use_mp3", False))
            max_duration = getattr(self.recorder, "max_duration", None)
        except Exception:
            rate = 16000
            channels = 1
            use_mp3 = False
            max_duration = None

        logger.info(
            "Pulse default source changed (%s -> %s); reopening audio stream",
            previous,
            current,
        )
        try:
            self.recorder.cleanup()
        except Exception:
            pass
        self.recorder = FastAudioRecorder(
            device_index=int(device_index) if device_index is not None else None,
            sample_rate=rate,
            channels=channels,
            use_mp3=use_mp3,
            max_duration=max_duration,
        )
        self._pulse_default_source = current
    
    def _find_working_audio_device(self):
        """Find a working audio input configuration (device + samplerate + channels)."""
        if sd is None:
            raise RuntimeError(
                "sounddevice is not installed; install it to use the recording daemon"
            )
        logger.info("Selecting audio input device...")
        preferred_samplerate = get_audio_sample_rate()
        preferred_channels = get_audio_channels()

        return resolve_audio_input(
            preferred_samplerate=preferred_samplerate,
            preferred_channels=preferred_channels,
        )

    def _initialize_audio(self):
        """Pre-initialize audio and recorder to reduce startup delay."""
        try:
            if sd is None:
                raise RuntimeError(
                    "sounddevice is not installed; install it to use the recording daemon"
                )
            # Find a working audio device automatically (best-effort).
            resolution = self._find_working_audio_device()
            selection = resolution.selection
            self.default_device = selection.device_index
            self.default_samplerate = selection.samplerate
            self.default_channels = selection.channels
            device_info = sd.query_devices(self.default_device, "input")
            logger.info(
                "Audio initialized (%s). Selected device %s: %s (samplerate=%s channels=%s max_amp=%s)",
                getattr(resolution, "source", "unknown"),
                self.default_device,
                getattr(resolution, "device_name", None) or device_info.get("name", ""),
                self.default_samplerate,
                self.default_channels,
                getattr(resolution, "max_amp", None),
            )
            
            # Pre-create recorder instance to avoid initialization delay
            self.recorder = FastAudioRecorder(
                device_index=self.default_device,
                sample_rate=int(self.default_samplerate or 16000),
                channels=int(self.default_channels or 1),
                use_mp3=False,
                max_duration=None,
            )
            self._update_pulse_default_source_snapshot()
            logger.info("Recorder pre-initialized for fast startup")
        except Exception as e:
            logger.warning("Could not pre-initialize audio: %s", e)
        
    def start(self):
        """Start the daemon service."""
        # Ensure parent dir exists for the socket path.
        try:
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # Clean up any existing socket
        if self.socket_path.exists():
            self.socket_path.unlink()
            
        # Create Unix domain socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.socket_path))
        try:
            os.chmod(self.socket_path, 0o600)
        except Exception:
            pass
        self.socket.listen(1)
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info("Voicepipe daemon started. Socket: %s", self.socket_path)
        
        # Main loop
        while self.running:
            try:
                conn, _ = self.socket.accept()
                threading.Thread(target=self._handle_client, args=(conn,)).start()
            except Exception as e:
                if self.running:
                    logger.exception("Error accepting connection: %s", e)
                    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info("Received signal %s, shutting down gracefully...", signum)
        with self._state_lock:
            self.running = False

            # Stop any active recording first
            if self.recording:
                logger.info("Stopping active recording...")
                try:
                    self._stop_recording()
                except Exception as e:
                    logger.exception("Error stopping recording: %s", e)

            # Clean up recorder
            if self.recorder:
                self.recorder.cleanup()
                self.recorder = None
        if self.socket:
            self.socket.close()
        if self.socket_path.exists():
            self.socket_path.unlink()
        
        logger.info("Daemon shutdown complete.")
        sys.exit(0)
        
    def _handle_client(self, conn):
        """Handle client requests."""
        try:
            conn.settimeout(2.0)
            data = b""
            request = None
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > 65536:
                    raise ValueError("Request too large")
                try:
                    request = json.loads(data.decode())
                    break
                except json.JSONDecodeError:
                    continue

            if not request:
                return

            command = request.get('command')
            
            with self._state_lock:
                if command == 'start':
                    response = self._start_recording(request.get('device'))
                elif command == 'stop':
                    response = self._stop_recording()
                elif command == 'cancel':
                    response = self._cancel_recording()
                elif command == 'status':
                    response = self._get_status()
                else:
                    response = {'error': f'Unknown command: {command}'}
                
            try:
                conn.sendall(json.dumps(response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
            
        except Exception as e:
            response = {'error': str(e)}
            try:
                conn.sendall(json.dumps(response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            conn.close()
            
    def _start_recording(self, device_index=None):
        """Start a new recording."""
        if self.recording:
            return {'error': 'Recording already in progress'}

        recorder_started = False
        try:
            if sd is None:
                return {
                    "error": (
                        "sounddevice is not installed; install it to record audio "
                        "(e.g. `pip install sounddevice`)"
                    )
                }
            apply_pulse_source_preference()
            device_index, device_error = parse_device_index(device_index)
            if device_error:
                return {"error": device_error}

            tmp_dir = audio_tmp_dir(create=True)

            # Create temp file
            fd, self.audio_file = tempfile.mkstemp(
                suffix=".wav",
                prefix="voicepipe_",
                dir=str(tmp_dir),
            )
            os.close(fd)
            
            # Use pre-initialized recorder if device matches, otherwise create new one
            if device_index is not None and device_index != self.default_device:
                # Different device requested, create new recorder
                if self.recorder:
                    self.recorder.cleanup()
                pref_sr = get_audio_sample_rate()
                pref_ch = get_audio_channels()
                selection = select_audio_input(
                    preferred_device_index=device_index,
                    preferred_samplerate=pref_sr,
                    preferred_channels=pref_ch,
                    strict_device_index=True,
                )
                self.recorder = FastAudioRecorder(
                    device_index=selection.device_index,
                    sample_rate=selection.samplerate,
                    channels=selection.channels,
                    use_mp3=False,
                    max_duration=None,
                )
                self._update_pulse_default_source_snapshot()
            elif not self.recorder:
                # No pre-initialized recorder, create one (best-effort selection).
                if self.default_device is None or self.default_samplerate is None or self.default_channels is None:
                    resolution = resolve_audio_input(
                        preferred_samplerate=get_audio_sample_rate(),
                        preferred_channels=get_audio_channels(),
                    )
                    selection = resolution.selection
                    self.default_device = selection.device_index
                    self.default_samplerate = selection.samplerate
                    self.default_channels = selection.channels
                self.recorder = FastAudioRecorder(
                    device_index=device_index if device_index is not None else self.default_device,
                    sample_rate=int(self.default_samplerate),
                    channels=int(self.default_channels),
                    use_mp3=False,
                    max_duration=None,
                )
                self._update_pulse_default_source_snapshot()

            # If we're using a Pulse/PipeWire backend without an explicit source pin,
            # refresh the pre-opened stream when the system default source changes.
            self._refresh_pulse_stream_if_default_changed()
            
            # Start recording with existing recorder
            self.recorder.start_recording(output_file=self.audio_file)
            recorder_started = True
            self.recording_id = uuid.uuid4().hex
            self.recording = True
            
            # Set up timeout (5 minutes)
            self.timeout_timer = threading.Timer(300, self._timeout_callback)
            self.timeout_timer.start()
            
            # Show systray icon if available
            try:
                systray = get_systray()
                # Use icon from project assets
                icon_path = Path(__file__).parent / "assets" / "recording_icon.tiff"
                if icon_path.exists():
                    systray.show(str(icon_path))
                else:
                    systray.show()  # Use default red recording icon
            except Exception as e:
                logger.debug("Systray init failed: %s", e)
            
            return {
                'status': 'recording',
                'audio_file': self.audio_file,
                'pid': os.getpid(),
                'recording_id': self.recording_id,
            }
            
        except Exception as e:
            try:
                if self.timeout_timer:
                    self.timeout_timer.cancel()
                    self.timeout_timer = None
            except Exception:
                pass

            if recorder_started and self.recorder:
                try:
                    self.recorder.stop_recording()
                except Exception:
                    pass
                try:
                    self.recorder.cleanup()
                except Exception:
                    pass
            elif self.recorder:
                try:
                    self.recorder.cleanup()
                except Exception:
                    pass

            self.recording = False
            self.recorder = None
            self.recording_id = None
            self._timeout_triggered = False

            try:
                get_systray().hide()
            except Exception:
                pass

            if self.audio_file and os.path.exists(self.audio_file):
                os.unlink(self.audio_file)
            self.audio_file = None
            return {'error': str(e)}
    
    def _timeout_callback(self):
        """Called when recording timeout is reached."""
        with self._state_lock:
            if self.recording:
                logger.info("Recording timeout reached (5 minutes), stopping...")
                self._timeout_triggered = True
                try:
                    self._stop_recording()
                except Exception as e:
                    logger.exception("Error during timeout handling: %s", e)
                    # Ensure we clean up gracefully even if stop fails
                    self._cleanup_timeout_state()
            
    def _stop_recording(self):
        """Stop recording and save audio."""
        if not self.recording:
            return {'error': 'No recording in progress'}
            
        try:
            recording_id = self.recording_id

            # Cancel timeout timer
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
            
            # Stop recording
            audio_data = self.recorder.stop_recording()
            if audio_data:
                # WAV mode fallback
                self.recorder.save_to_file(audio_data, self.audio_file)
            self.recorder.cleanup()
            
            # Hide systray icon
            try:
                get_systray().hide()
            except Exception:
                pass
            
            response = {
                'status': 'stopped',
                'audio_file': self.audio_file,
                'recording_id': recording_id,
            }
            
            # Handle file cleanup - only delete on timeout, preserve for transcription
            if self._timeout_triggered:
                # Timeout completion - delete the file
                if self.audio_file and os.path.exists(self.audio_file):
                    os.unlink(self.audio_file)
            # If normal completion, preserve the file for transcription
            
            # Reset state
            self.recording = False
            self.recorder = None
            self.audio_file = None
            self.recording_id = None
            self._timeout_triggered = False
            
            return response
            
        except Exception as e:
            return {'error': str(e)}
            
    def _cancel_recording(self):
        """Cancel current recording."""
        if not self.recording:
            return {'error': 'No recording in progress'}
            
        try:
            recording_id = self.recording_id

            # Cancel timeout timer
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
            
            # Stop and cleanup
            if self.recorder:
                self.recorder.stop_recording()
                self.recorder.cleanup()
                
            # Always remove audio file on explicit cancel
            if self.audio_file and os.path.exists(self.audio_file):
                os.unlink(self.audio_file)
                
            # Reset state
            self.recording = False
            self.recorder = None
            self.audio_file = None
            self.recording_id = None
            self._timeout_triggered = False
            
            # Hide systray icon
            try:
                get_systray().hide()
            except Exception:
                pass
            
            return {'status': 'cancelled', 'recording_id': recording_id}
            
        except Exception as e:
            return {'error': str(e)}
            
    def _get_status(self):
        """Get current daemon status."""
        return {
            'status': 'recording' if self.recording else 'idle',
            'pid': os.getpid(),
            'audio_file': self.audio_file,
            'recording_id': self.recording_id,
        }

    def _cleanup_timeout_state(self):
        """Clean up state after timeout without deleting files."""
        try:
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
            
            if self.recorder:
                try:
                    self.recorder.stop_recording()
                    self.recorder.cleanup()
                except Exception as e:
                    logger.exception("Error cleaning up recorder: %s", e)
            
            # Hide systray icon
            try:
                get_systray().hide()
            except Exception:
                pass
            
            # Reset state but preserve audio file
            self.recording = False
            self.recorder = None
            # Keep audio_file reference so the file isn't deleted
            
        except Exception as e:
            logger.exception("Error in cleanup_timeout_state: %s", e)


def main():
    """Run the daemon."""
    configure_logging(default_level=logging.INFO)
    daemon = RecordingDaemon()
    daemon.start()


if __name__ == '__main__':
    main()
