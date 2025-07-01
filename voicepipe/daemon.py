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

# Platform-specific imports for IPC
if sys.platform == "win32":
    import win32pipe
    import win32file
    import pywintypes # For exception handling
else:
    import fcntl # For non-blocking socket on Unix


class RecordingDaemon:
    """Background daemon that handles recording requests."""
    
    if sys.platform == "win32":
        NAMED_PIPE_PATH = r'\\.\pipe\voicepipe_daemon'
        SOCKET_PATH = None  # Not used on Windows
    else:
        SOCKET_PATH = Path(tempfile.gettempdir()) / 'voicepipe.sock'
        NAMED_PIPE_PATH = None # Not used on Unix-like systems
    
    def __init__(self):
        self.recorder = None
        self.recording = False
        self.audio_file = None
        self.running = True
        self.timeout_timer = None
        self.pyaudio = None
        self.default_device = None
        self.pipe_handle = None # For Windows named pipe
        self.socket = None # For Unix domain socket
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
        # Set up signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, self._signal_handler)

        if sys.platform == "win32":
            self._start_windows_ipc()
        else:
            self._start_unix_ipc()

    def _start_windows_ipc(self):
        """Start IPC using Named Pipes on Windows."""
        print(f"Voicepipe daemon starting on Windows. Pipe: {self.NAMED_PIPE_PATH}", file=sys.stderr)
        while self.running:
            try:
                self.pipe_handle = win32pipe.CreateNamedPipe(
                    self.NAMED_PIPE_PATH,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    65536, 65536, 0, None
                )
                if self.pipe_handle == win32file.INVALID_HANDLE_VALUE:
                    if self.running: # Avoid error message if shutting down
                        print(f"Error creating named pipe: {win32api.GetLastError()}", file=sys.stderr)
                        time.sleep(1) # Avoid busy loop if CreateNamedPipe fails repeatedly
                        continue

                print(f"Named pipe created. Waiting for client connection...", file=sys.stderr)
                win32pipe.ConnectNamedPipe(self.pipe_handle, None)
                print(f"Client connected to named pipe.", file=sys.stderr)
                # Pass the handle to a new thread, then loop to create a new pipe instance for the next client
                threading.Thread(target=self._handle_client_windows, args=(self.pipe_handle,)).start()
                self.pipe_handle = None # Reset for the next instance
            except pywintypes.error as e:
                if e.winerror == 232: # ERROR_NO_DATA (Pipe closing)
                    print("Pipe closing, likely client disconnected or daemon shutdown.", file=sys.stderr)
                elif self.running: # Don't print errors if we are shutting down
                    print(f"Windows named pipe error: {e}", file=sys.stderr)
                if self.running:
                    time.sleep(0.1) # Avoid busy loop on persistent errors
            except Exception as e:
                if self.running:
                    print(f"Unexpected error in Windows IPC loop: {e}", file=sys.stderr)
                if self.running:
                    time.sleep(0.1) # Avoid busy loop
        print("Windows IPC loop ended.", file=sys.stderr)


    def _start_unix_ipc(self):
        """Start IPC using Unix Domain Sockets."""
        # Clean up any existing socket
        if self.SOCKET_PATH.exists():
            try:
                self.SOCKET_PATH.unlink()
            except OSError as e:
                print(f"Error removing existing socket {self.SOCKET_PATH}: {e}", file=sys.stderr)
                return # Cannot proceed if socket cleanup fails
            
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.bind(str(self.SOCKET_PATH))
            self.socket.listen(5) # Increased backlog
            # Set socket to non-blocking
            fl = fcntl.fcntl(self.socket.fileno(), fcntl.F_GETFL)
            fcntl.fcntl(self.socket.fileno(), fcntl.F_SETFL, fl | os.O_NONBLOCK)
        except Exception as e:
            print(f"Error binding or listening to socket {self.SOCKET_PATH}: {e}", file=sys.stderr)
            self.running = False # Stop if socket setup fails
            return

        print(f"Voicepipe daemon started. Socket: {self.SOCKET_PATH}", file=sys.stderr)
        
        while self.running:
            try:
                conn, _ = self.socket.accept()
                conn.setblocking(True) # Operations on client socket should be blocking
                threading.Thread(target=self._handle_client_unix, args=(conn,)).start()
            except BlockingIOError:
                # This is expected in non-blocking mode when no connection is pending
                time.sleep(0.1) # Wait a bit before trying accept() again
                continue
            except Exception as e:
                if self.running: # Don't print errors if we are shutting down
                    print(f"Error accepting Unix socket connection: {e}", file=sys.stderr)
                if self.running:
                    time.sleep(0.1) # Avoid busy loop

    def _shutdown_ipc(self):
        """Gracefully shuts down IPC mechanisms."""
        print("Shutting down IPC...", file=sys.stderr)
        if sys.platform == "win32":
            if self.pipe_handle and self.pipe_handle != win32file.INVALID_HANDLE_VALUE:
                try:
                    # To unblock ConnectNamedPipe, another thread needs to connect to the pipe.
                    # Or, more simply, just close the handle.
                    # If a client is connected, it will get an error.
                    # If ConnectNamedPipe is waiting, it might raise an exception.
                    print(f"Closing active pipe handle {self.pipe_handle}", file=sys.stderr)
                    win32file.CloseHandle(self.pipe_handle)
                    self.pipe_handle = None
                except pywintypes.error as e:
                    print(f"Error closing pipe handle: {e}", file=sys.stderr)
            # To ensure the main loop CreatingNamedPipe exits, we can try to connect to it
            # This is a bit hacky, a dedicated event might be better for future.
            try:
                # Create a dummy client connection to unblock the CreateNamedPipe/ConnectNamedPipe call
                # This is only if the loop is stuck waiting for a client.
                # A more robust solution would use an event to signal shutdown.
                client_pipe = win32file.CreateFile(
                    self.NAMED_PIPE_PATH,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None
                )
                if client_pipe != win32file.INVALID_HANDLE_VALUE:
                    print("Dummy client connected to unblock server pipe.", file=sys.stderr)
                    # Send a shutdown command if the protocol supports it, or just close.
                    # For now, just closing is fine as the self.running flag will be false.
                    win32file.CloseHandle(client_pipe)
                else:
                    # This might happen if the pipe server wasn't fully up or already down.
                    print(f"Could not connect dummy client to {self.NAMED_PIPE_PATH}, error: {win32api.GetLastError()}", file=sys.stderr)

            except pywintypes.error as e:
                 # ERROR_PIPE_BUSY (231) can happen if server is spinning up/down.
                 # ERROR_FILE_NOT_FOUND (2) if pipe doesn't exist.
                if e.winerror not in [2, 231]: # Ignore if pipe not found or busy
                    print(f"Error in dummy client connection during shutdown: {e}", file=sys.stderr)

        else: # Unix
            if self.socket:
                try:
                    self.socket.close()
                    print("Unix socket closed.", file=sys.stderr)
                except Exception as e:
                    print(f"Error closing Unix socket: {e}", file=sys.stderr)
            if self.SOCKET_PATH and self.SOCKET_PATH.exists():
                try:
                    self.SOCKET_PATH.unlink()
                    print(f"Unix socket file {self.SOCKET_PATH} unlinked.", file=sys.stderr)
                except OSError as e:
                    print(f"Error unlinking socket file {self.SOCKET_PATH}: {e}", file=sys.stderr)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        print(f"Received signal {signum}, initiating shutdown...", file=sys.stderr)
        self.running = False
        # The main IPC loop should now exit. Call _shutdown_ipc to clean up.
        self._shutdown_ipc() # Attempt to close sockets/pipes to unblock accept/connect calls

        if self.recorder and self.recording: # Ensure recording is stopped
            print("Stopping active recording due to signal...", file=sys.stderr)
            self._stop_recording_internal() # Use internal stop to avoid sending response

        if self.pyaudio:
            self.pyaudio.terminate()
            print("PyAudio terminated.", file=sys.stderr)
        
        # Wait for threads to finish? This can be complex.
        # For now, rely on daemon threads and quick exit.
        print("Exiting daemon.", file=sys.stderr)
        sys.exit(0)

    def _handle_client_common(self, request_data):
        """Common logic for handling client requests, independent of IPC type."""
        try:
            request = json.loads(request_data)
            command = request.get('command')

            if command == 'start':
                return self._start_recording(request.get('device'))
            elif command == 'stop':
                return self._stop_recording()
            elif command == 'cancel':
                return self._cancel_recording()
            elif command == 'status':
                return self._get_status()
            elif command == 'shutdown': # New command
                self.running = False
                self._shutdown_ipc() # Close IPC from within the handler
                # Acknowledge shutdown, then other parts of signal_handler will take over
                return {'status': 'shutting down'}
            else:
                return {'error': f'Unknown command: {command}'}
        except json.JSONDecodeError as e:
            return {'error': f'Invalid JSON request: {e}'}
        except Exception as e:
            # Log the full error for debugging on the daemon side
            print(f"Error processing command '{request.get('command', 'unknown')}': {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return {'error': f'Internal server error: {e}'}


    def _handle_client_windows(self, pipe):
        """Handle client requests on Windows named pipe."""
        try:
            # Read request
            # ReadFile can return (0, b'') if pipe is closed by client during read
            hr, data_bytes = win32file.ReadFile(pipe, 4096) # Read up to 4KB
            if hr != 0: # Should be 0 for success or ERROR_MORE_DATA
                 print(f"ReadFile error: {hr}", file=sys.stderr)
                 return # Or send error response?

            if not data_bytes:
                print("Client sent empty data or disconnected before sending.", file=sys.stderr)
                return

            request_data = data_bytes.decode('utf-8')
            print(f"Received on pipe: {request_data}", file=sys.stderr)

            response = self._handle_client_common(request_data)

            # Send response
            response_bytes = json.dumps(response).encode('utf-8')
            win32file.WriteFile(pipe, response_bytes)

        except pywintypes.error as e:
            if e.winerror == 109: # ERROR_BROKEN_PIPE (client disconnected)
                print(f"Client disconnected (broken pipe): {e}", file=sys.stderr)
            elif e.winerror == 233: # ERROR_PIPE_NOT_CONNECTED
                print(f"Pipe not connected (client likely disconnected before write): {e}", file=sys.stderr)
            else:
                print(f"Windows pipe communication error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error handling Windows client: {e}", file=sys.stderr)
            # Attempt to send an error response if possible
            try:
                error_response = json.dumps({'error': f'Server error: {e}'}).encode('utf-8')
                win32file.WriteFile(pipe, error_response)
            except Exception as send_error:
                print(f"Could not send error to client: {send_error}", file=sys.stderr)
        finally:
            win32file.FlushFileBuffers(pipe)
            win32pipe.DisconnectNamedPipe(pipe)
            win32file.CloseHandle(pipe)
            print(f"Closed pipe handle for client.", file=sys.stderr)

    def _handle_client_unix(self, conn):
        """Handle client requests on Unix domain socket."""
        try:
            data = conn.recv(4096).decode('utf-8') # Increased buffer size
            if not data:
                print("Client sent empty data or disconnected (Unix).", file=sys.stderr)
                return

            print(f"Received on socket: {data}", file=sys.stderr)
            response = self._handle_client_common(data)
                
            conn.sendall(json.dumps(response).encode('utf--8')) # Use sendall
            
        except socket.error as e:
            print(f"Unix socket communication error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Error handling Unix client: {e}", file=sys.stderr)
            # Attempt to send an error response
            try:
                error_response = json.dumps({'error': f'Server error: {e}'}).encode('utf-8')
                conn.sendall(error_response)
            except Exception as send_error:
                print(f"Could not send error to client (Unix): {send_error}", file=sys.stderr)
        finally:
            try:
                conn.shutdown(socket.SHUT_RDWR) # Graceful shutdown
            except socket.error:
                pass # Ignore if already closed
            conn.close()
            print(f"Closed socket connection for client.", file=sys.stderr)
            
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
                pyaudio_instance=self.pyaudio, # This instance is created in __init__
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
            icon_to_show = str(icon_path) if icon_path.exists() else None

            # Pass self (daemon instance) to systray.show for callbacks
            systray.show(daemon_instance=self, icon_path=icon_to_show)
            
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
            self._stop_recording_internal() # Internal call, doesn't return for IPC
            # This callback is from a timer thread, can't send IPC response here.
            # Client would discover via status or if attempting a new command.
            
    def _stop_recording_internal(self, save_audio=True):
        """Internal logic to stop recording, optionally saving the audio."""
        if not self.recording:
            return False # Indicate not recording or already stopped

        print("Executing internal stop recording logic...", file=sys.stderr)
        try:
            # Cancel timeout timer
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
            
            # Stop recording
            if self.recorder:
                audio_data = self.recorder.stop_recording() # Returns None if MP3 was written directly
                if save_audio and audio_data and self.audio_file: # WAV mode fallback or if direct save failed
                    print(f"Saving audio data to {self.audio_file}", file=sys.stderr)
                    self.recorder.save_to_file(audio_data, self.audio_file)
                self.recorder.cleanup()
                print("Recorder cleaned up.", file=sys.stderr)
            
            # Hide systray icon
            get_systray().hide()
            print("Systray hidden.", file=sys.stderr)

            if not save_audio and self.audio_file and os.path.exists(self.audio_file):
                print(f"Deleting audio file {self.audio_file} as per cancel.", file=sys.stderr)
                os.unlink(self.audio_file)
            
            return True # Indicate successful stop
        except Exception as e:
            print(f"Error during internal stop recording: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            return False # Indicate error during stop
        finally:
            # Reset state regardless of save_audio, but keep audio_file if meant to be saved
            self.recording = False
            self.recorder = None
            if not save_audio: # If cancelling, clear audio_file path
                self.audio_file = None
            print("Internal stop recording state reset.", file=sys.stderr)


    def _stop_recording(self):
        """Stop recording and save audio. (IPC Exposed)"""
        if not self.recording:
            return {'error': 'No recording in progress'}

        audio_file_path = self.audio_file # Preserve for response
        if self._stop_recording_internal(save_audio=True):
            return {
                'status': 'stopped',
                'audio_file': audio_file_path
            }
        else:
            # If internal stop failed, some resources might not be cleaned.
            # State is reset by _stop_recording_internal's finally block.
            return {'error': 'Failed to stop recording cleanly. Check daemon logs.'}
            
    def _cancel_recording(self):
        """Cancel current recording. (IPC Exposed)"""
        if not self.recording:
            return {'error': 'No recording in progress'}
            
        if self._stop_recording_internal(save_audio=False):
            return {'status': 'cancelled'}
        else:
            return {'error': 'Failed to cancel recording cleanly. Check daemon logs.'}
            
    def _get_status(self):
        """Get current daemon status."""
        return {
            'status': 'recording' if self.recording else 'idle',
            'pid': os.getpid(),
            'audio_file': self.audio_file
        }

    def cancel_recording_via_ui(self):
        """Called by systray UI to cancel current recording."""
        print("cancel_recording_via_ui called on daemon instance.", file=sys.stderr)
        if not self.recording:
            print("Not recording, UI cancel ignored.", file=sys.stderr)
            return {'error': 'No recording in progress'}

        if self._stop_recording_internal(save_audio=False):
            print("Recording cancelled successfully via UI.", file=sys.stderr)
            return {'status': 'cancelled'}
        else:
            print("Failed to cancel recording via UI.", file=sys.stderr)
            return {'error': 'Failed to cancel recording cleanly via UI. Check daemon logs.'}


def main():
    """Run the daemon."""
    daemon = RecordingDaemon()
    daemon.start()


if __name__ == '__main__':
    main()