"""Core recording functionality for voicepipe."""

import os
import sys
import json
import signal
import tempfile
import wave
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import pyaudio

from .systray import get_systray


class FastAudioRecorder:
    """Optimized audio recorder that accepts pre-initialized PyAudio."""
    
    def __init__(self, pyaudio_instance=None, device_index=None, use_mp3=True, max_duration=300):
        self.audio = pyaudio_instance or pyaudio.PyAudio()
        self.device_index = device_index
        self.stream = None
        self.frames = []
        self.recording = False
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000
        self.use_mp3 = use_mp3
        self.ffmpeg_process = None
        self.mp3_file = None
        self.max_duration = max_duration
        self.start_time = None
        self.timeout_timer = None
        self.owns_pyaudio = pyaudio_instance is None
        
    def start_recording(self, output_file=None):
        """Start recording with minimal delay."""
        device = self.device_index if self.device_index is not None else None
        
        try:
            # Start stream immediately - don't wait for ffmpeg
            self.stream = self.audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=device,
                frames_per_buffer=self.chunk,
                stream_callback=self._audio_callback,
                start=False  # Don't start yet
            )
            
            self.frames = []
            self.recording = True
            
            # Start ffmpeg in parallel if needed
            if self.use_mp3 and output_file:
                self.mp3_file = output_file
                self._start_ffmpeg_async(output_file)
            
            # Now start the stream
            self.stream.start_stream()
            
            # Set up timeout
            self.start_time = time.time()
            if self.max_duration:
                self.timeout_timer = threading.Timer(self.max_duration, self._timeout_callback)
                self.timeout_timer.start()
                
        except Exception as e:
            raise RuntimeError(f"Failed to start recording: {e}")
    
    def _start_ffmpeg_async(self, output_file):
        """Start ffmpeg process asynchronously."""
        def start_ffmpeg():
            self.ffmpeg_process = subprocess.Popen([
                'ffmpeg',
                '-f', 's16le',
                '-ar', str(self.rate),
                '-ac', str(self.channels),
                '-i', '-',
                '-acodec', 'mp3',
                '-b:a', '64k',
                '-y',
                output_file
            ], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        threading.Thread(target=start_ffmpeg, daemon=True).start()
        
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio stream."""
        if self.recording:
            if self.ffmpeg_process and self.ffmpeg_process.stdin:
                try:
                    self.ffmpeg_process.stdin.write(in_data)
                except:
                    pass
            else:
                self.frames.append(in_data)
        return (in_data, pyaudio.paContinue)
    
    def _timeout_callback(self):
        """Called when recording timeout is reached."""
        if self.recording:
            print(f"Recording timeout reached ({self.max_duration}s), stopping...", file=sys.stderr)
            os.kill(os.getpid(), signal.SIGTERM)
    
    def stop_recording(self):
        """Stop recording and return the recorded data."""
        if not self.recording:
            return None
            
        self.recording = False
        
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            
        if self.ffmpeg_process:
            self.ffmpeg_process.stdin.close()
            self.ffmpeg_process.wait()
            return None
        else:
            return b''.join(self.frames)
    
    def save_to_file(self, audio_data, filename):
        """Save audio data to WAV file."""
        wf = wave.open(filename.replace('.mp3', '.wav'), 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(self.audio.get_sample_size(self.format))
        wf.setframerate(self.rate)
        wf.writeframes(audio_data)
        wf.close()
    
    def cleanup(self):
        """Clean up resources."""
        if self.timeout_timer:
            self.timeout_timer.cancel()
        if self.stream:
            self.stream.close()
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except:
                pass
        if self.owns_pyaudio:
            self.audio.terminate()


class AudioRecorder(FastAudioRecorder):
    """Handles audio recording to temporary MP3 files."""
    
    def __init__(self, device_index=None, use_mp3=True, max_duration=300):
        self.audio = pyaudio.PyAudio()
        self.device_index = device_index
        self.stream = None
        self.frames = []
        self.recording = False
        self.chunk = 1024
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 16000  # 16kHz is good for speech
        self.use_mp3 = use_mp3
        self.ffmpeg_process = None
        self.mp3_file = None
        self.max_duration = max_duration  # 5 minutes default
        self.start_time = None
        self.timeout_timer = None
        
    def get_default_device(self):
        """Get the default input device index."""
        try:
            info = self.audio.get_default_input_device_info()
            return info['index']
        except Exception:
            return 0
            
    def start_recording(self, output_file=None):
        """Start recording audio."""
        device = self.device_index if self.device_index is not None else self.get_default_device()
        
        try:
            if self.use_mp3 and output_file:
                # Start ffmpeg process for direct MP3 encoding
                self.mp3_file = output_file
                self.ffmpeg_process = subprocess.Popen([
                    'ffmpeg',
                    '-f', 's16le',  # raw PCM input
                    '-ar', str(self.rate),  # sample rate
                    '-ac', str(self.channels),  # channels
                    '-i', '-',  # read from stdin
                    '-acodec', 'mp3',
                    '-b:a', '64k',  # 64kbps for voice
                    '-y',  # overwrite output file
                    output_file
                ], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                
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
            
            # Set up timeout
            self.start_time = time.time()
            if self.max_duration:
                self.timeout_timer = threading.Timer(self.max_duration, self._timeout_callback)
                self.timeout_timer.start()
        except Exception as e:
            raise RuntimeError(f"Failed to start recording: {e}")
    
    def _timeout_callback(self):
        """Called when recording timeout is reached."""
        if self.recording:
            print(f"Recording timeout reached ({self.max_duration}s), stopping...", file=sys.stderr)
            # Signal the main process to stop
            os.kill(os.getpid(), signal.SIGTERM)
    
    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback for audio stream."""
        if self.recording:
            if self.ffmpeg_process and self.ffmpeg_process.stdin:
                try:
                    self.ffmpeg_process.stdin.write(in_data)
                except:
                    pass  # Handle broken pipe gracefully
            else:
                self.frames.append(in_data)
        return (in_data, pyaudio.paContinue)
    
    def stop_recording(self):
        """Stop recording and return the recorded data."""
        if not self.recording:
            return None
            
        self.recording = False
        
        # Cancel timeout timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
        
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
            self.stream = None
            
        if self.ffmpeg_process:
            # Close ffmpeg stdin and wait for it to finish
            self.ffmpeg_process.stdin.close()
            self.ffmpeg_process.wait()
            self.ffmpeg_process = None
            return None  # MP3 already written to file
            
        # Make sure we have audio data (WAV mode)
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
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except:
                pass
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
        fd, audio_file = tempfile.mkstemp(suffix='.mp3', prefix='voicepipe_')
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