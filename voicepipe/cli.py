"""Command-line interface for voicepipe."""

import os
import sys
import signal
import socket
import json
import subprocess
import tempfile # For gettempdir
from pathlib import Path

import click

from .recorder import AudioRecorder, RecordingSession
from .transcriber import WhisperTranscriber
# RecordingDaemon is imported for the `daemon` command, not directly for IPC constants here.
from .daemon import RecordingDaemon

# Define IPC Paths similar to daemon.py for the client to use
if sys.platform == "win32":
    NAMED_PIPE_PATH = r'\\.\pipe\voicepipe_daemon'
    SOCKET_PATH = None
    import win32pipe
    import win32file
    import pywintypes # Required for pywintypes.error
else:
    SOCKET_PATH = Path(tempfile.gettempdir()) / 'voicepipe.sock'
    NAMED_PIPE_PATH = None


def daemon_request(command, **kwargs):
    """Send a request to the daemon service using appropriate IPC."""
    request_payload = json.dumps({'command': command, **kwargs}).encode('utf-8')
    
    if sys.platform == "win32":
        try:
            pipe_handle = win32file.CreateFile(
                NAMED_PIPE_PATH,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None
            )

            # Set pipe to message mode (optional, but good practice if server expects it)
            # This might not be strictly necessary if server handles byte streams robustly.
            # res = win32pipe.SetNamedPipeHandleState(pipe_handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
            # if not res:
            #     print(f"Warning: Failed to set named pipe handle state: {pywintypes.WinError()}", file=sys.stderr)

            win32file.WriteFile(pipe_handle, request_payload)

            # Read response
            # Loop to read potentially fragmented messages, though for typical short JSON, one read is often enough.
            response_data = b""
            while True:
                hr, data = win32file.ReadFile(pipe_handle, 4096)
                response_data += data
                if hr == 0: # ERROR_SUCCESS, read complete for this chunk
                    # If server uses WriteFile without specific message framing,
                    # client might need to know response size or use a delimiter.
                    # Assuming server sends one complete JSON response.
                    break
                elif hr == 109: # ERROR_BROKEN_PIPE
                    print(f"Warning: Pipe broken while reading response from daemon.", file=sys.stderr)
                    win32file.CloseHandle(pipe_handle)
                    return None
                elif hr != 0: # Some other error
                    print(f"Warning: Error reading from pipe: {hr} - {pywintypes.WinError(hr)}", file=sys.stderr)
                    win32file.CloseHandle(pipe_handle)
                    return None

            win32file.CloseHandle(pipe_handle)
            return json.loads(response_data.decode('utf-8'))

        except pywintypes.error as e:
            # Common errors:
            # 2 (ERROR_FILE_NOT_FOUND): Pipe doesn't exist (daemon not running or wrong path)
            # 231 (ERROR_PIPE_BUSY): Pipe is busy (shouldn't happen with CreateFile if server handles instances)
            if e.winerror == 2: # File not found, daemon likely not running
                # This is the expected case for fallback, so no warning here.
                pass
            else:
                print(f"Warning: WindowsNamedPipeError - Could not connect to daemon ({e.winerror}): {e.strerror}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Warning: Could not connect to daemon (Windows): {e}", file=sys.stderr)
            return None

    else: # Unix-like platforms
        if not SOCKET_PATH or not SOCKET_PATH.exists():
            # Daemon not running or socket path issue
            return None

        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(SOCKET_PATH))

            client.sendall(request_payload) # Use sendall

            # It's good practice to loop recv for sockets too, but for small JSON, one might be okay.
            # However, server might send in chunks.
            response_parts = []
            while True:
                part = client.recv(4096)
                if not part:
                    break # Connection closed
                response_parts.append(part)
                # Heuristic: if part is small and ends with '}', assume full JSON received.
                # A more robust way is Content-Length header or newline delimiter if server supports.
                if part.strip().endswith(b'}') and len(part) < 4096 :
                    try: # Try to decode to see if it's complete JSON
                        json.loads(b"".join(response_parts).decode('utf-8'))
                        break
                    except json.JSONDecodeError:
                        if len(response_parts) > 10 : # Safety break after many parts
                             print(f"Warning: Potentially incomplete JSON from daemon (Unix)", file=sys.stderr)
                             break
                        continue # Incomplete, continue reading

            client.close()

            if not response_parts:
                print(f"Warning: No response from daemon (Unix)", file=sys.stderr)
                return None

            return json.loads(b"".join(response_parts).decode('utf-8'))

        except socket.error as e:
            # Common errors: ENOENT (socket file not found), ECONNREFUSED (daemon not listening)
            if e.errno == socket.errno.ENOENT or e.errno == socket.errno.ECONNREFUSED:
                 # Expected if daemon not running, no warning needed for fallback.
                pass
            else:
                print(f"Warning: UnixSocketError - Could not connect to daemon ({e.errno}): {os.strerror(e.errno)}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Warning: Could not connect to daemon (Unix): {e}", file=sys.stderr)
            return None


def run_recording_subprocess():
    """Run the actual recording in a subprocess (fallback when no daemon)."""
    recorder = None
    session = None
    try:
        session = RecordingSession.create_session()
        
        # Set up signal handlers
        def signal_handler(signum, frame):
            if recorder and recorder.recording:
                try:
                    audio_data = recorder.stop_recording()
                    if audio_data:
                        recorder.save_to_file(audio_data, session['audio_file'])
                except Exception as e:
                    print(f"Error saving audio: {e}", file=sys.stderr)
            if recorder:
                recorder.cleanup()
            sys.exit(0)
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        # Get device from environment or use default
        device = os.environ.get('VOICEPIPE_DEVICE')
        device_index = int(device) if device and device.isdigit() else None
        
        recorder = AudioRecorder(device_index=device_index)
        
        print(f"Recording started (PID: {os.getpid()})...", file=sys.stderr)
        recorder.start_recording(output_file=session['audio_file'])
        
        # Block until terminated
        signal.pause()
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if recorder:
            recorder.cleanup()
        sys.exit(1)


@click.group()
def main():
    """Voicepipe - Voice recording and transcription CLI tool."""
    pass


@main.command()
@click.option('--device', envvar='VOICEPIPE_DEVICE', help='Audio device index to use')
def start(device):
    """Start recording audio from microphone."""
    try:
        # Try daemon first
        response = daemon_request('start', device=device)
        if response:
            if 'error' in response:
                print(f"Error: {response['error']}", file=sys.stderr)
                sys.exit(1)
            else:
                print(f"Recording started (daemon mode)")
                return
        
        # Fallback to subprocess method
        # Check for existing sessions
        active = RecordingSession.find_active_sessions()
        if active:
            print(f"Error: Recording already in progress (PID: {active[0]['pid']})", file=sys.stderr)
            sys.exit(1)
        
        # Start recording in a subprocess
        env = os.environ.copy()
        if device:
            env['VOICEPIPE_DEVICE'] = str(device)
        
        # Use sys.executable to ensure we use the same Python interpreter
        proc = subprocess.Popen(
            [sys.executable, '-m', 'voicepipe.cli', '_record'],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Wait a moment to check if it started successfully
        import time
        time.sleep(0.5)
        
        if proc.poll() is not None:
            # Process already exited
            stderr = proc.stderr.read()
            print(f"Error starting recording: {stderr}", file=sys.stderr)
            sys.exit(1)
        
        print(f"Recording started (PID: {proc.pid})")
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
@click.option('--type', is_flag=True, help='Type the transcribed text using xdotool')
@click.option('--language', help='Language code for transcription (e.g., en, es, fr)')
@click.option('--prompt', help='Context prompt to guide transcription style. For dictation with quotes, say "open quote" and "close quote"')
@click.option('--model', default='gpt-4o-transcribe', help='Transcription model to use (default: gpt-4o-transcribe, options: gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1)')
@click.option('--temperature', default=0.0, type=float, help='Temperature for transcription (0.0 for deterministic, default: 0.0)')
def stop(type, language, prompt, model, temperature):
    """Stop recording and transcribe the audio."""
    try:
        # Try daemon first
        response = daemon_request('stop')
        if response:
            if 'error' in response:
                print(f"Error: {response['error']}", file=sys.stderr)
                sys.exit(1)
            audio_file = response.get('audio_file')
        else:
            # Fallback to subprocess method
            # Get active session
            session = RecordingSession.get_current_session()
            pid = session['pid']
            audio_file = session['audio_file']
            
            # Send SIGTERM to recording process
            try:
                os.kill(pid, signal.SIGTERM)
                # Wait a moment for the process to finish
                import time
                time.sleep(0.5)
            except ProcessLookupError:
                pass  # Process already gone
        
        # Transcribe the audio
        try:
            transcriber = WhisperTranscriber(model=model)
            text = transcriber.transcribe(audio_file, language=language, prompt=prompt, temperature=temperature)
            
            # Output to stdout
            print(text)
            
            # Output to stdout
            print(text)

            # Type if requested
            if type:
                if sys.platform == "win32":
                    try:
                        import pyautogui
                        print("Typing text using pyautogui (Windows)...", file=sys.stderr)
                        pyautogui.typewrite(text, interval=0.01)
                        print("Typing complete.", file=sys.stderr)
                    except ImportError:
                        print("Error: pyautogui library not found. Please install with the [typing] extra (e.g., pip install 'voicepipe[typing]').", file=sys.stderr)
                    except Exception as e:
                        print(f"Error using pyautogui to type text on Windows: {e}", file=sys.stderr)
                elif sys.platform == "darwin": # macOS
                    try:
                        import pyautogui
                        print("Typing text using pyautogui (macOS)...", file=sys.stderr)
                        pyautogui.typewrite(text, interval=0.01)
                        print("Typing complete.", file=sys.stderr)
                    except ImportError:
                        print("Error: pyautogui library not found. Please install with the [typing] extra (e.g., pip install 'voicepipe[typing]').", file=sys.stderr)
                    except Exception as e:
                        # Catching all exceptions from pyautogui on macOS is important due to potential permission issues.
                        print(f"Error using pyautogui to type text on macOS: {e}", file=sys.stderr)
                        print("IMPORTANT: On macOS, you may need to grant Accessibility permissions to your terminal or Python.", file=sys.stderr)
                        print("Go to System Settings > Privacy & Security > Accessibility, and add your terminal application or the Python executable used.", file=sys.stderr)
                elif sys.platform.startswith("linux"):
                    try:
                        # Use xdotool to type the text
                        subprocess.run(
                            ['xdotool', 'type', '--', text],
                            capture_output=True,
                            text=True,
                            check=True
                        )
                    except subprocess.CalledProcessError as e:
                        error_msg = f"Error typing text with xdotool: {e}"
                        print(error_msg, file=sys.stderr)
                        subprocess.run(['xdotool', 'type', '--', error_msg], capture_output=True, text=True) # Try to type the error
                    except FileNotFoundError:
                        error_msg = "Error: xdotool not found. Please install it (e.g., sudo apt install xdotool or sudo pacman -S xdotool)."
                        print(error_msg, file=sys.stderr)
                else:
                    print(f"Warning: --type functionality is not implemented for this platform ({sys.platform}). Text was not typed.", file=sys.stderr)
                    
        except Exception as e:
            error_msg = str(e)
            print(f"Error during transcription or typing: {error_msg}", file=sys.stderr)
            sys.exit(1)
        finally:
            # Clean up session (only for subprocess method)
            if not response:
                RecordingSession.cleanup_session(session)
            # Clean up audio file
            if audio_file and os.path.exists(audio_file):
                os.unlink(audio_file)
            
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
def status():
    """Check recording status."""
    try:
        # Try daemon first
        response = daemon_request('status')
        if response:
            if 'error' in response:
                print(f"Error: {response['error']}", file=sys.stderr)
                sys.exit(1)
            else:
                status = response.get('status', 'unknown')
                if status == 'recording':
                    print(f"Status: recording (daemon mode)")
                else:
                    print(f"Status: {status}")
                return
        
        # Fallback to subprocess method
        try:
            session = RecordingSession.get_current_session()
            print(f"Status: recording (PID: {session['pid']})")
        except RuntimeError:
            print("Status: not recording")
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
def cancel():
    """Cancel active recording without transcribing."""
    try:
        # Try daemon first
        response = daemon_request('cancel')
        if response:
            if 'error' in response:
                print(f"Error: {response['error']}", file=sys.stderr)
                sys.exit(1)
            else:
                print("Recording cancelled")
                return
        
        # Fallback to subprocess method
        # Get active session
        session = RecordingSession.get_current_session()
        pid = session['pid']
        
        # Send SIGTERM to recording process
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass  # Process already gone
        
        # Clean up session
        RecordingSession.cleanup_session(session)
        
        print("Recording cancelled")
        
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
def daemon():
    """Run the voicepipe daemon service."""
    try:
        daemon = RecordingDaemon()
        daemon.start()
    except KeyboardInterrupt:
        print("\nDaemon stopped by user")
    except Exception as e:
        print(f"Daemon error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command('_record', hidden=True)
def _record():
    """Internal command to run recording subprocess."""
    run_recording_subprocess()


if __name__ == '__main__':
    main()