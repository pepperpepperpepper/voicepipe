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

import pyaudio

from .recorder import FastAudioRecorder
from .systray import get_systray


class RecordingDaemon:
    """Background daemon that handles recording requests."""
    
    SOCKET_PATH = Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / 'voicepipe.sock'
    
    def __init__(self):
        self.recorder = None
        self.recording = False
        self.audio_file = None
        self.running = True
        self.timeout_timer = None
        self.pyaudio = None
        self.default_device = None
        self._initialize_audio()
    
    def _initialize_audio(self):
        """Pre-initialize PyAudio to reduce startup delay."""
        try:
            self.pyaudio = pyaudio.PyAudio()
            # Get default device index
            info = self.pyaudio.get_default_input_device_info()
            self.default_device = info['index']
            print(f"Audio initialized. Default device: {info['name']}", file=sys.stderr)
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
        self.running = False
        if self.recorder:
            self.recorder.cleanup()
        if self.pyaudio:
            self.pyaudio.terminate()
        if self.socket:
            self.socket.close()
        if self.SOCKET_PATH.exists():
            self.SOCKET_PATH.unlink()
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
                
            conn.send(json.dumps(response).encode())
            
        except Exception as e:
            response = {'error': str(e)}
            conn.send(json.dumps(response).encode())
        finally:
            conn.close()
            
    def _start_recording(self, device_index=None):
        """Start a new recording."""
        if self.recording:
            return {'error': 'Recording already in progress'}
            
        try:
            # Create temp file
            fd, self.audio_file = tempfile.mkstemp(suffix='.mp3', prefix='voicepipe_')
            os.close(fd)
            
            # Start recording with pre-initialized PyAudio
            self.recorder = FastAudioRecorder(
                pyaudio_instance=self.pyaudio,
                device_index=device_index or self.default_device
            )
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
            self._stop_recording()
            
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
            
            # Reset state
            self.recording = False
            self.recorder = None
            self.audio_file = None
            
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
                
            # Remove audio file
            if self.audio_file and os.path.exists(self.audio_file):
                os.unlink(self.audio_file)
                
            # Reset state
            self.recording = False
            self.recorder = None
            self.audio_file = None
            
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


def main():
    """Run the daemon."""
    daemon = RecordingDaemon()
    daemon.start()


if __name__ == '__main__':
    main()