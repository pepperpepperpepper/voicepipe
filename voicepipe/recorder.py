"""Core recording functionality for voicepipe."""

import os
import sys
import json
import signal
import tempfile
import wave
from datetime import datetime
from pathlib import Path

import pyaudio


class AudioRecorder:
    """Handles audio recording to temporary WAV files."""
    
    def __init__(self, device_index=None):
        self.audio = pyaudio.PyAudio()
        self.device_index = device_index
        self.stream = None
        self.frames = []
        self.recording = False
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000  # 16kHz is good for speech
        
    def get_default_device(self):
        """Get the default input device index."""
        try:
            info = self.audio.get_default_input_device_info()
            return info['index']
        except Exception:
            return 0
            
    def start_recording(self):
        """Start recording audio."""
        device = self.device_index if self.device_index is not None else self.get_default_device()
        
        try:
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=device,
                frames_per_buffer=self.chunk,
                stream_callback=self._audio_callback
            )
            self.frames = []
            self.recording = True
            self.stream.start_stream()
        except Exception as e:
            raise RuntimeError(f"Failed to start recording: {e}")
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio stream."""
        if self.recording:
            self.frames.append(in_data)
        return (in_data, pyaudio.paContinue)
    
    def stop_recording(self):
        """Stop recording and return the recorded data."""
        if not self.recording:
            return None
            
        self.recording = False
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            
        # Make sure we have audio data
        if not self.frames:
            raise RuntimeError("No audio data recorded")
            
        return b''.join(self.frames)
    
    def save_to_file(self, data, filepath):
        """Save recorded data to a WAV file."""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.audio.get_sample_size(self.format))
            wf.setframerate(self.rate)
            wf.writeframes(data)
    
    def cleanup(self):
        """Clean up audio resources."""
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        self.audio.terminate()


class RecordingSession:
    """Manages recording sessions with PID tracking."""
    
    STATE_DIR = Path("/tmp")
    STATE_PREFIX = "voicepipe-"
    
    @classmethod
    def get_state_file(cls, pid=None):
        """Get the state file path for a given PID."""
        if pid is None:
            pid = os.getpid()
        return cls.STATE_DIR / f"{cls.STATE_PREFIX}{pid}.json"
    
    @classmethod
    def find_active_sessions(cls):
        """Find all active recording sessions."""
        sessions = []
        for file in cls.STATE_DIR.glob(f"{cls.STATE_PREFIX}*.json"):
            try:
                with open(file, 'r') as f:
                    data = json.load(f)
                    pid = data.get('pid')
                    # Check if process is still running
                    if pid and cls._is_process_running(pid):
                        sessions.append(data)
                    else:
                        # Clean up stale session
                        file.unlink(missing_ok=True)
                        if 'audio_file' in data:
                            Path(data['audio_file']).unlink(missing_ok=True)
            except Exception:
                # Invalid or corrupted state file
                file.unlink(missing_ok=True)
        return sessions
    
    @classmethod
    def _is_process_running(cls, pid):
        """Check if a process with given PID is running."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    
    @classmethod
    def create_session(cls):
        """Create a new recording session."""
        # Check for existing sessions
        active = cls.find_active_sessions()
        if active:
            raise RuntimeError(f"Recording already in progress (PID: {active[0]['pid']})")
        
        # Create temporary audio file
        fd, audio_file = tempfile.mkstemp(suffix='.wav', prefix='voicepipe_')
        os.close(fd)  # We'll write to it later
        
        # Create session data
        session = {
            'pid': os.getpid(),
            'audio_file': audio_file,
            'started_at': datetime.now().isoformat(),
        }
        
        # Save state file
        state_file = cls.get_state_file()
        with open(state_file, 'w') as f:
            json.dump(session, f)
        
        return session
    
    @classmethod
    def get_current_session(cls):
        """Get the current active session."""
        sessions = cls.find_active_sessions()
        if not sessions:
            raise RuntimeError("No active recording session found")
        return sessions[0]
    
    @classmethod
    def cleanup_session(cls, session):
        """Clean up a recording session."""
        # Remove state file
        state_file = cls.get_state_file(session['pid'])
        state_file.unlink(missing_ok=True)
        
        # Remove audio file
        if 'audio_file' in session:
            Path(session['audio_file']).unlink(missing_ok=True)