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
from pathlib import Path

import sounddevice as sd

from .device import parse_device_index
from .logging_utils import configure_logging
from .paths import audio_tmp_dir, daemon_socket_path
from .recorder import FastAudioRecorder
from .systray import get_systray

logger = logging.getLogger(__name__)

class RecordingDaemon:
    """Background daemon that handles recording requests."""
    
    SOCKET_PATH = daemon_socket_path()
    
    def __init__(self):
        self._state_lock = threading.Lock()
        self.socket = None
        self.recorder = None
        self.recording = False
        self.audio_file = None
        self.running = True
        self.timeout_timer = None
        self.default_device = None
        self._timeout_triggered = False
        self._initialize_audio()
    
    def _find_working_audio_device(self):
        """Find an input device that can open at 16kHz.

        Prefer the system's default input (and common virtual devices like
        PipeWire/Pulse) before trying everything else.
        """
        logger.info("Selecting audio input device...")

        candidates = []

        # Prefer explicit VOICEPIPE_DEVICE if set (works for systemd too).
        env_device = os.environ.get("VOICEPIPE_DEVICE")
        if env_device and env_device.isdigit():
            candidates.append(int(env_device))

        # Prefer sounddevice's default input.
        try:
            default_in = sd.default.device[0]
            if default_in is not None and int(default_in) >= 0:
                candidates.append(int(default_in))
        except Exception:
            pass

        devices = sd.query_devices()
        preferred_tokens = (" default", "pulse", "pipewire")

        # Then prefer names like "default", "pulse", "pipewire".
        for device_index, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            name = str(device.get("name", "")).lower()
            if any(tok.strip() in name for tok in preferred_tokens):
                candidates.append(device_index)

        # Finally, try everything else with input channels.
        for device_index, device in enumerate(devices):
            if device.get("max_input_channels", 0) <= 0:
                continue
            candidates.append(device_index)

        # Deduplicate while preserving order.
        seen = set()
        ordered = []
        for idx in candidates:
            if idx in seen:
                continue
            seen.add(idx)
            ordered.append(idx)

        for device_index in ordered:
            try:
                name = sd.query_devices(device_index, "input")["name"]
            except Exception:
                name = str(device_index)

            try:
                logger.debug("Testing device %s: %s", device_index, name)
                with sd.InputStream(
                    device=device_index,
                    channels=1,
                    samplerate=16000,
                    dtype="int16",
                    blocksize=1024,
                ) as stream:
                    stream.read(1024)

                logger.info("Using device %s: %s", device_index, name)
                return device_index
            except Exception as e:
                logger.debug("Device %s failed: %s", device_index, e)
                continue

        logger.warning("No working input device found; falling back to device 0")
        return 0

    def _initialize_audio(self):
        """Pre-initialize audio and recorder to reduce startup delay."""
        try:
            # Find a working audio device automatically
            self.default_device = self._find_working_audio_device()
            device_info = sd.query_devices(self.default_device, 'input')
            logger.info(
                "Audio initialized. Selected device %s: %s",
                self.default_device,
                device_info.get("name", ""),
            )
            
            # Pre-create recorder instance to avoid initialization delay
            self.recorder = FastAudioRecorder(
                device_index=self.default_device,
                use_mp3=False,
                max_duration=None,
            )
            logger.info("Recorder pre-initialized for fast startup")
        except Exception as e:
            logger.warning("Could not pre-initialize audio: %s", e)
        
    def start(self):
        """Start the daemon service."""
        # Ensure parent dir exists for the socket path.
        try:
            self.SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        # Clean up any existing socket
        if self.SOCKET_PATH.exists():
            self.SOCKET_PATH.unlink()
            
        # Create Unix domain socket
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.SOCKET_PATH))
        self.socket.listen(1)
        
        # Set up signal handlers
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
        
        logger.info("Voicepipe daemon started. Socket: %s", self.SOCKET_PATH)
        
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
        if self.SOCKET_PATH.exists():
            self.SOCKET_PATH.unlink()
        
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

        try:
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
                self.recorder = FastAudioRecorder(
                    device_index=device_index,
                    use_mp3=False,
                    max_duration=None,
                )
            elif not self.recorder:
                # No pre-initialized recorder, create one
                self.recorder = FastAudioRecorder(
                    device_index=device_index if device_index is not None else self.default_device,
                    use_mp3=False,
                    max_duration=None,
                )
            
            # Start recording with existing recorder
            self.recorder.start_recording(output_file=self.audio_file)
            self.recording = True
            
            # Set up timeout (5 minutes)
            self.timeout_timer = threading.Timer(300, self._timeout_callback)
            self.timeout_timer.start()
            
            # Show systray icon if available
            systray = get_systray()
            # Use icon from project assets
            icon_path = Path(__file__).parent / "assets" / "recording_icon.tiff"
            if icon_path.exists():
                systray.show(str(icon_path))
            else:
                systray.show()  # Use default red recording icon
            
            return {
                'status': 'recording',
                'audio_file': self.audio_file,
                'pid': os.getpid()
            }
            
        except Exception as e:
            if self.audio_file and os.path.exists(self.audio_file):
                os.unlink(self.audio_file)
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
            get_systray().hide()
            
            response = {
                'status': 'stopped',
                'audio_file': self.audio_file
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
            self._timeout_triggered = False
            
            return response
            
        except Exception as e:
            return {'error': str(e)}
            
    def _cancel_recording(self):
        """Cancel current recording."""
        if not self.recording:
            return {'error': 'No recording in progress'}
            
        try:
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
            self._timeout_triggered = False
            
            # Hide systray icon
            get_systray().hide()
            
            return {'status': 'cancelled'}
            
        except Exception as e:
            return {'error': str(e)}
            
    def _get_status(self):
        """Get current daemon status."""
        return {
            'status': 'recording' if self.recording else 'idle',
            'pid': os.getpid(),
            'audio_file': self.audio_file
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
            get_systray().hide()
            
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
