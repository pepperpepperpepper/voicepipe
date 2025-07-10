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
import queue

import sounddevice as sd
import numpy as np

from .systray import get_systray

# Windows-specific constant for hiding console windows
if sys.platform == "win32":
    subprocess.CREATE_NO_WINDOW = 0x08000000


def get_temp_dir():
    """Get temp directory, preferring RAM disk if available."""
    # Check for RAM disk environment variable
    ram_temp = os.environ.get('VOICEPIPE_TEMP')
    if ram_temp and os.path.exists(ram_temp):
        return ram_temp
    
    # Check for R: drive directly on Windows
    if sys.platform == "win32" and os.path.exists('R:\\voicepipe_temp'):
        return 'R:\\voicepipe_temp'
    
    # Fall back to system temp
    return tempfile.gettempdir()


class FastAudioRecorder:
    """Optimized audio recorder using sounddevice."""
    
    def __init__(self, device_index=None, use_mp3=True, max_duration=300):
        self.device_index = device_index
        self.stream = None
        self.audio_queue = queue.Queue()
        self.recording = False
        self.format = np.int16
        self.channels = 1
        self.rate = 16000
        self.use_mp3 = use_mp3
        self.ffmpeg_process = None
        self.mp3_file = None
        self.max_duration = max_duration
        self.start_time = None
        self.timeout_timer = None
        
    def start_recording(self, output_file=None):
        """Start recording with minimal delay."""
        try:
            # Start ffmpeg in parallel if needed
            if self.use_mp3 and output_file:
                self.mp3_file = output_file
                self._start_ffmpeg_async(output_file)
            
            self.recording = True
            
            # Start sounddevice stream
            self.stream = sd.InputStream(
                device=self.device_index,
                channels=self.channels,
                samplerate=self.rate,
                dtype=self.format,
                callback=self._audio_callback,
                blocksize=1024
            )
            self.stream.start()
            
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
            kwargs = {
                'stdin': subprocess.PIPE,
                'stderr': subprocess.DEVNULL,
                'stdout': subprocess.DEVNULL
            }
            
            # Hide console window on Windows
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                kwargs['startupinfo'] = startupinfo
                kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
            
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
            ], **kwargs)
        
        threading.Thread(target=start_ffmpeg, daemon=True).start()
        
    def _audio_callback(self, indata, frames, time, status):
        """Callback for audio stream."""
        if status:
            print(f"Audio callback status: {status}", file=sys.stderr)
        
        if self.recording:
            audio_data = indata.copy()
            if self.ffmpeg_process and self.ffmpeg_process.stdin:
                try:
                    self.ffmpeg_process.stdin.write(audio_data.tobytes())
                except:
                    pass
            else:
                self.audio_queue.put(audio_data.tobytes())
    
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
            self.stream.stop()
            self.stream.close()
            self.stream = None
            
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.stdin.close()
            except:
                pass
            
            # Give ffmpeg a chance to exit cleanly
            try:
                self.ffmpeg_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                # Force terminate if it doesn't exit
                self.ffmpeg_process.terminate()
                try:
                    self.ffmpeg_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # Kill if terminate didn't work
                    self.ffmpeg_process.kill()
                    self.ffmpeg_process.wait()
            
            self.ffmpeg_process = None
            return None
        else:
            # Collect all audio data from queue
            frames = []
            while not self.audio_queue.empty():
                frames.append(self.audio_queue.get())
            return b''.join(frames)
    
    def save_to_file(self, audio_data, filename):
        """Save audio data to WAV file."""
        wf = wave.open(filename.replace('.mp3', '.wav'), 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(2)  # 2 bytes for int16
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
            except:
                pass
            
            try:
                self.ffmpeg_process.terminate()
                self.ffmpeg_process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.ffmpeg_process.kill()
                self.ffmpeg_process.wait()
            except:
                pass
            
            self.ffmpeg_process = None


class AudioRecorder(FastAudioRecorder):
    """Handles audio recording to temporary MP3 files."""
    
    def __init__(self, device_index=None, use_mp3=True, max_duration=300):
        self.device_index = device_index
        self.stream = None
        self.audio_queue = queue.Queue()
        self.recording = False
        self.format = np.int16
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
            return sd.default.device[0]  # Input device
        except Exception:
            return None
            
    def start_recording(self, output_file=None):
        """Start recording audio."""
        device = self.device_index if self.device_index is not None else self.get_default_device()
        
        try:
            if self.use_mp3 and output_file:
                # Start ffmpeg process for direct MP3 encoding
                self.mp3_file = output_file
                
                kwargs = {
                    'stdin': subprocess.PIPE,
                    'stderr': subprocess.DEVNULL,
                    'stdout': subprocess.DEVNULL
                }
                
                # Hide console window on Windows
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    kwargs['startupinfo'] = startupinfo
                    kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
                
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
                ], **kwargs)
            
            self.recording = True
            
            # Start sounddevice stream
            self.stream = sd.InputStream(
                device=device,
                channels=self.channels,
                samplerate=self.rate,
                dtype=self.format,
                callback=self._audio_callback,
                blocksize=1024
            )
            self.stream.start()
            
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
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback for audio stream."""
        if status:
            print(f"Audio callback status: {status}", file=sys.stderr)
            
        if self.recording:
            audio_data = indata.copy()
            if self.ffmpeg_process and self.ffmpeg_process.stdin:
                try:
                    self.ffmpeg_process.stdin.write(audio_data.tobytes())
                except:
                    pass  # Handle broken pipe gracefully
            else:
                self.audio_queue.put(audio_data.tobytes())
    
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
            self.stream.stop()
            self.stream.close()
            self.stream = None
            
        if self.ffmpeg_process:
            # Close ffmpeg stdin and wait for it to finish
            self.ffmpeg_process.stdin.close()
            self.ffmpeg_process.wait()
            self.ffmpeg_process = None
            return None  # MP3 already written to file
            
        # Collect all audio data from queue
        frames = []
        while not self.audio_queue.empty():
            frames.append(self.audio_queue.get())
            
        # Make sure we have audio data (WAV mode)
        if not frames:
            raise RuntimeError("No audio data recorded")
            
        return b''.join(frames)
    
    def save_to_file(self, data, filepath):
        """Save recorded data to a WAV file."""
        with wave.open(filepath, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 2 bytes for int16
            wf.setframerate(self.rate)
            wf.writeframes(data)
    
    def cleanup(self):
        """Clean up audio resources."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
        if self.ffmpeg_process:
            try:
                self.ffmpeg_process.stdin.close()
                self.ffmpeg_process.terminate()
            except:
                pass


class RecordingSession:
    """Manages recording sessions with PID tracking."""
    
    STATE_DIR = Path(get_temp_dir())
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
        
        # Create temporary audio file in RAM disk if available
        fd, audio_file = tempfile.mkstemp(suffix='.mp3', prefix='voicepipe_', dir=get_temp_dir())
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