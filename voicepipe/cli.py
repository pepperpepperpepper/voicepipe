"""Command-line interface for voicepipe."""

import os
import sys
import signal
import socket
import json
import subprocess
from pathlib import Path

import click

from .recorder import AudioRecorder, RecordingSession
from .transcriber import WhisperTranscriber
from .daemon import RecordingDaemon


def daemon_request(command, **kwargs):
    """Send a request to the daemon service."""
    socket_path = RecordingDaemon.SOCKET_PATH
    
    # Check if daemon is running
    if not socket_path.exists():
        # Try the subprocess method as fallback
        return None
        
    try:
        # Connect to daemon
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(socket_path))
        
        # Send request
        request = {'command': command, **kwargs}
        client.send(json.dumps(request).encode())
        
        # Get response
        response = client.recv(4096).decode()
        client.close()
        
        return json.loads(response)
        
    except Exception as e:
        print(f"Warning: Could not connect to daemon: {e}", file=sys.stderr)
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
        recorder.start_recording()
        
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
def stop(type, language):
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
            transcriber = WhisperTranscriber()
            text = transcriber.transcribe(audio_file, language=language)
            
            # Output to stdout
            print(text)
            
            # Type if requested
            if type:
                try:
                    # Use xdotool to type the text
                    result = subprocess.run(
                        ['xdotool', 'type', '--', text],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                except subprocess.CalledProcessError as e:
                    error_msg = f"Error typing text: {e}"
                    print(error_msg, file=sys.stderr)
                    # Try to type the error message
                    subprocess.run(
                        ['xdotool', 'type', '--', error_msg],
                        capture_output=True
                    )
                except FileNotFoundError:
                    error_msg = "Error: xdotool not found. Install with: sudo pacman -S xdotool"
                    print(error_msg, file=sys.stderr)
                    
        except Exception as e:
            error_msg = str(e)
            print(f"Error: {error_msg}", file=sys.stderr)
            if type:
                # Try to type the error
                try:
                    subprocess.run(
                        ['xdotool', 'type', '--', f"Error: {error_msg}"],
                        capture_output=True
                    )
                except:
                    pass
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


@main.command('_record', hidden=True)
def _record():
    """Internal command to run recording subprocess."""
    run_recording_subprocess()


if __name__ == '__main__':
    main()