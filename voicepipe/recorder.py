"""Core recording functionality for voicepipe."""

import os
import json
import signal
import tempfile
import wave
import subprocess
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
import queue

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover
    sd = None
import numpy as np

from .paths import audio_tmp_dir, session_state_dir
from .systray import get_systray

logger = logging.getLogger(__name__)

def _require_sounddevice() -> None:
    if sd is None:
        raise RuntimeError(
            "sounddevice is not installed; install it to record audio (e.g. `pip install sounddevice`)"
        )

class FastAudioRecorder:
    """Optimized audio recorder using sounddevice."""
    
    def __init__(self, device_index=None, use_mp3=False, max_duration=300, pre_open=True):
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
        self._timeout_triggered = False
        
        # Pre-open stream for faster start
        if pre_open:
            self._pre_open_stream()
    
    def _pre_open_stream(self):
        """Pre-open the audio stream but don't start recording yet."""
        _require_sounddevice()
        try:
            self.stream = sd.InputStream(
                device=self.device_index,
                channels=self.channels,
                samplerate=self.rate,
                dtype=self.format,
                callback=self._audio_callback,
                blocksize=1024
            )
            # Don't start it yet, just have it ready
        except Exception as e:
            logger.warning("Could not pre-open stream: %s", e)
            self.stream = None
        
    def start_recording(self, output_file=None):
        """Start recording with minimal delay."""
        _require_sounddevice()
        try:
            # Start ffmpeg in parallel if needed
            if self.use_mp3 and output_file:
                self.mp3_file = output_file
                self._start_ffmpeg_async(output_file)
            
            self.recording = True
            
            # Use pre-opened stream if available, otherwise create new one
            if not self.stream:
                self.stream = sd.InputStream(
                    device=self.device_index,
                    channels=self.channels,
                    samplerate=self.rate,
                    dtype=self.format,
                    callback=self._audio_callback,
                    blocksize=1024
                )
            
            # Start the stream
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
        
    def _audio_callback(self, indata, frames, time, status):
        """Callback for audio stream."""
        if status:
            logger.debug("Audio callback status: %s", status)
        
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
            logger.info("Recording timeout reached (%ss), stopping...", self.max_duration)
            # Instead of killing the process, gracefully stop recording
            try:
                self.stop_recording()
            except Exception as e:
                logger.exception("Error during timeout handling: %s", e)
                self.cleanup()
    
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
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
            except Exception:
                pass

            # Give ffmpeg a moment to flush/finalize the output, but never hang.
            try:
                self.ffmpeg_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    self.ffmpeg_process.terminate()
                except Exception:
                    pass
                try:
                    self.ffmpeg_process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        self.ffmpeg_process.kill()
                    except Exception:
                        pass
                    try:
                        self.ffmpeg_process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
            return None
        else:
            # Collect all audio data from queue
            frames = []
            while not self.audio_queue.empty():
                frames.append(self.audio_queue.get())
            if not frames:
                raise RuntimeError("No audio data recorded")
            return b''.join(frames)
    
    def save_to_file(self, audio_data, filename):
        """Save audio data to a WAV file."""
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 2 bytes for int16
            wf.setframerate(self.rate)
            wf.writeframes(audio_data)
    
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
                # Wait briefly for graceful termination
                self.ffmpeg_process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                self.ffmpeg_process.kill()
                try:
                    self.ffmpeg_process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            except:
                pass


class AudioRecorder(FastAudioRecorder):
    """Audio recorder (WAV by default)."""
    
    def __init__(self, device_index=None, use_mp3=False, max_duration=300):
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
        _require_sounddevice()
        try:
            return sd.default.device[0]  # Input device
        except Exception:
            return None
            
    def start_recording(self, output_file=None):
        """Start recording audio."""
        _require_sounddevice()
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
            logger.info("Recording timeout reached (%ss), stopping...", self.max_duration)
            # Instead of killing the process, gracefully stop recording
            try:
                self.stop_recording()
            except Exception as e:
                logger.exception("Error during timeout handling: %s", e)
                self.cleanup()
    
    def _audio_callback(self, indata, frames, time, status):
        """Callback for audio stream."""
        if status:
            logger.debug("Audio callback status: %s", status)
            
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
            try:
                if self.ffmpeg_process.stdin:
                    self.ffmpeg_process.stdin.close()
            except Exception:
                pass

            # Give ffmpeg time to flush/finalize the MP3, but never hang forever.
            try:
                self.ffmpeg_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                try:
                    self.ffmpeg_process.terminate()
                except Exception:
                    pass
                try:
                    self.ffmpeg_process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    try:
                        self.ffmpeg_process.kill()
                    except Exception:
                        pass
                    try:
                        self.ffmpeg_process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass

            if self.ffmpeg_process.poll() is not None:
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
                # Wait briefly for graceful termination
                self.ffmpeg_process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # Force kill if it doesn't terminate gracefully
                self.ffmpeg_process.kill()
                try:
                    self.ffmpeg_process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            except:
                pass

