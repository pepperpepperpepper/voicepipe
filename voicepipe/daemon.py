"""Daemon service for voicepipe recording."""

import os
import sys
import signal
import socket
import json
import threading
import tempfile
import time
from pathlib import Path

import sounddevice as sd

from .recorder import FastAudioRecorder
from .systray import get_systray


def _runtime_dir() -> Path:
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir:
        return Path(xdg_runtime_dir)
    run_user_dir = Path("/run/user") / str(os.getuid())
    if run_user_dir.exists():
        return run_user_dir
    return Path("/tmp")


class RecordingDaemon:
    """Background daemon that handles recording requests."""
    
    SOCKET_PATH = _runtime_dir() / "voicepipe.sock"
    
    def __init__(self):
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
        print("Selecting audio input device...", file=sys.stderr)

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
                print(f"Testing device {device_index}: {name}", file=sys.stderr)
                with sd.InputStream(
                    device=device_index,
                    channels=1,
                    samplerate=16000,
                    dtype="int16",
                    blocksize=1024,
                ) as stream:
                    stream.read(1024)

                print(f"✓ Using device {device_index}: {name}", file=sys.stderr)
                return device_index
            except Exception as e:
                print(f"✗ Device {device_index} failed: {e}", file=sys.stderr)
                continue

        print("No working input device found, falling back to device 0", file=sys.stderr)
        return 0

    def _initialize_audio(self):
        """Pre-initialize audio and recorder to reduce startup delay."""
        try:
            # Find a working audio device automatically
            self.default_device = self._find_working_audio_device()
            device_info = sd.query_devices(self.default_device, 'input')
            print(f"Audio initialized. Selected device {self.default_device}: {device_info['name']}", file=sys.stderr)
            
            # Pre-create recorder instance to avoid initialization delay
            self.recorder = FastAudioRecorder(device_index=self.default_device)
            print("Recorder pre-initialized for fast startup", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Could not pre-initialize audio: {e}", file=sys.stderr)
        
    def start(self):
        """Start the daemon service."""
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
        
        print(f"Voicepipe daemon started. Socket: {self.SOCKET_PATH}", file=sys.stderr)
        
        # Main loop
        while self.running:
            try:
                conn, _ = self.socket.accept()
                threading.Thread(target=self._handle_client, args=(conn,)).start()
            except Exception as e:
                if self.running:
                    print(f"Error accepting connection: {e}", file=sys.stderr)
                    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"Received signal {signum}, shutting down gracefully...", file=sys.stderr)
        self.running = False
        
        # Stop any active recording first
        if self.recording:
            print("Stopping active recording...", file=sys.stderr)
            try:
                self._stop_recording()
            except Exception as e:
                print(f"Error stopping recording: {e}", file=sys.stderr)
        
        # Clean up recorder
        if self.recorder:
            self.recorder.cleanup()
        if self.socket:
            self.socket.close()
        if self.SOCKET_PATH.exists():
            self.SOCKET_PATH.unlink()
        
        print("Daemon shutdown complete.", file=sys.stderr)
        sys.exit(0)
        
    def _handle_client(self, conn):
        """Handle client requests."""
        try:
            data = conn.recv(1024).decode()
            if not data:
                return
                
            request = json.loads(data)
            command = request.get('command')
            
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
                conn.send(json.dumps(response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
            
        except Exception as e:
            response = {'error': str(e)}
            try:
                conn.send(json.dumps(response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        finally:
            conn.close()
            
    def _start_recording(self, device_index=None):
        """Start a new recording."""
        if self.recording:
            return {'error': 'Recording already in progress'}
            
        try:
            # Ensure temp directory exists
            Path('/tmp/voicepipe').mkdir(parents=True, exist_ok=True)

            # Create temp file
            fd, self.audio_file = tempfile.mkstemp(suffix='.mp3', prefix='voicepipe_', dir='/tmp/voicepipe')
            os.close(fd)
            
            # Use pre-initialized recorder if device matches, otherwise create new one
            if device_index and device_index != self.default_device:
                # Different device requested, create new recorder
                if self.recorder:
                    self.recorder.cleanup()
                self.recorder = FastAudioRecorder(device_index=device_index)
            elif not self.recorder:
                # No pre-initialized recorder, create one
                self.recorder = FastAudioRecorder(
                    device_index=device_index or self.default_device
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
        if self.recording:
            print("Recording timeout reached (5 minutes), stopping...", file=sys.stderr)
            self._timeout_triggered = True
            try:
                self._stop_recording()
            except Exception as e:
                print(f"Error during timeout handling: {e}", file=sys.stderr)
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
                    print(f"Error cleaning up recorder: {e}", file=sys.stderr)
            
            # Hide systray icon
            get_systray().hide()
            
            # Reset state but preserve audio file
            self.recording = False
            self.recorder = None
            # Keep audio_file reference so the file isn't deleted
            
        except Exception as e:
            print(f"Error in cleanup_timeout_state: {e}", file=sys.stderr)


def main():
    """Run the daemon."""
    daemon = RecordingDaemon()
    daemon.start()


if __name__ == '__main__':
    main()
