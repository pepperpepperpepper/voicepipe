"""Command-line interface for voicepipe."""

import os
import sys
import signal
import socket
import json
import subprocess
import time
import shutil
from pathlib import Path

import click

from .audio import select_audio_input
from .recorder import AudioRecorder, RecordingSession
from .transcriber import WhisperTranscriber
from .daemon import RecordingDaemon


def _runtime_dir() -> Path:
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir:
        return Path(xdg_runtime_dir)
    run_user_dir = Path("/run/user") / str(os.getuid())
    if run_user_dir.exists():
        return run_user_dir
    return Path("/tmp")


def _get_preferred_sample_rate() -> int:
    for name in ("VOICEPIPE_AUDIO_SAMPLE_RATE", "VOICEPIPE_SAMPLE_RATE"):
        raw = os.environ.get(name)
        if not raw:
            continue
        try:
            value = int(str(raw).strip())
        except Exception:
            continue
        if value > 0:
            return value
    return 16000


def _get_preferred_channels() -> int:
    for name in ("VOICEPIPE_AUDIO_CHANNELS", "VOICEPIPE_CHANNELS"):
        raw = os.environ.get(name)
        if not raw:
            continue
        try:
            value = int(str(raw).strip())
        except Exception:
            continue
        if value > 0:
            return value
    return 1


def _find_existing_socket(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.exists():
                return path
        except Exception:
            continue
    return None


def _daemon_socket_candidates() -> list[Path]:
    candidates: list[Path] = []
    for name in ("VOICEPIPE_DAEMON_SOCKET", "VOICEPIPE_SOCKET_PATH"):
        raw = (os.environ.get(name) or "").strip()
        if raw:
            try:
                candidates.append(Path(raw).expanduser())
            except Exception:
                pass
    # Legacy default (this branch) and newer per-app runtime dir layout.
    candidates.append(_runtime_dir() / "voicepipe.sock")
    candidates.append(_runtime_dir() / "voicepipe" / "voicepipe.sock")
    # Extra legacy locations seen in the wild.
    candidates.append(Path("/tmp") / "voicepipe.sock")
    return candidates


def daemon_request(command, **kwargs):
    """Send a request to the daemon service."""
    socket_path = _find_existing_socket(_daemon_socket_candidates())
    
    # Check if daemon is running
    if not socket_path:
        # Try the subprocess method as fallback
        return None
        
    client = None
    try:
        # Connect to daemon with short connect timeout
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.5)
        client.connect(str(socket_path))
        
        # Send request
        request = {'command': command, **kwargs}
        client.send(json.dumps(request).encode())
        
        # Allow longer response time for start/stop operations
        read_timeout = 0.5 if command == 'status' else 5.0
        client.settimeout(read_timeout)

        # Get response (handle partial reads)
        response_data = b""
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response_data += chunk
            try:
                return json.loads(response_data.decode())
            except json.JSONDecodeError:
                continue

        if response_data:
            return json.loads(response_data.decode())
        return None
        
    except Exception as e:
        print(f"Warning: Could not connect to daemon: {e}", file=sys.stderr)
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


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
        
        selection = select_audio_input(
            preferred_device_index=device_index,
            preferred_samplerate=_get_preferred_sample_rate(),
            preferred_channels=_get_preferred_channels(),
            strict_device_index=bool(device_index is not None),
        )
        recorder = AudioRecorder(
            device_index=selection.device_index,
            sample_rate=selection.samplerate,
            channels=selection.channels,
        )
        
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
            
            # Type if requested
            if type:
                try:
                    # Use xdotool to type the text
                    subprocess.run(
                        ['xdotool', 'type', '--clearmodifiers', '--', text],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                except subprocess.CalledProcessError as e:
                    error_msg = f"Error typing text: {e}"
                    print(error_msg, file=sys.stderr)
                    # Try to type the error message
                    subprocess.run(
                        ['xdotool', 'type', '--clearmodifiers', '--', error_msg],
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
                        ['xdotool', 'type', '--clearmodifiers', '--', f"Error: {error_msg}"],
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
            print("Status: idle")
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command("transcribe-file")
@click.argument("audio_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--type", is_flag=True, help="Type the transcribed text using xdotool")
@click.option("--language", help="Language code for transcription (e.g., en, es, fr)")
@click.option(
    "--prompt",
    help=(
        "Context prompt to guide transcription style. For dictation with quotes, "
        'say "open quote" and "close quote"'
    ),
)
@click.option(
    "--model",
    default="gpt-4o-transcribe",
    help=(
        "Transcription model to use (default: gpt-4o-transcribe, options: "
        "gpt-4o-transcribe, gpt-4o-mini-transcribe, whisper-1)"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
def transcribe_file(type, audio_file, language, prompt, model, temperature):
    """Transcribe an audio file (no recording session required)."""
    try:
        transcriber = WhisperTranscriber(model=model)
        text = transcriber.transcribe(
            audio_file, language=language, prompt=prompt, temperature=temperature
        )
        print(text)

        if type:
            subprocess.run(["xdotool", "type", "--", text], check=False)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
@click.option("--audio-test", is_flag=True, help="Record 0.5s and report levels")
@click.option("--record-test", is_flag=True, help="Start/stop a 1s daemon recording and report file size")
@click.option("--transcribe-test", is_flag=True, help="Transcribe the record-test audio file")
@click.option(
    "--record-seconds",
    default=1.0,
    type=float,
    show_default=True,
    help="Seconds to record for --record-test",
)
@click.option(
    "--play",
    is_flag=True,
    help="Play the record-test file via ffplay (if available)",
)
def doctor(audio_test, record_test, transcribe_test, record_seconds, play):
    """Print diagnostics for common Voicepipe issues."""
    socket_path = RecordingDaemon.SOCKET_PATH

    print(f"python: {sys.executable}")
    print(f"cwd: {os.getcwd()}")
    print(f"XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '')}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '')}")
    print(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '')}")

    tmp_dir = Path("/tmp/voicepipe")
    print(f"/tmp/voicepipe exists: {tmp_dir.exists()}")
    print(f"daemon socket exists: {socket_path} {socket_path.exists()}")

    # API key presence (never print the key)
    key_env = os.environ.get("OPENAI_API_KEY")
    key_file = Path.home() / ".config" / "voicepipe" / "api_key"
    key_alt = Path.home() / ".voicepipe_api_key"
    print(f"OPENAI_API_KEY env set: {bool(key_env)}")
    print(f"~/.config/voicepipe/api_key exists: {key_file.exists()}")
    print(f"~/.voicepipe_api_key exists: {key_alt.exists()}")

    ffmpeg_path = shutil.which("ffmpeg")
    xdotool_path = shutil.which("xdotool")
    print(f"ffmpeg found: {bool(ffmpeg_path)}")
    print(f"xdotool found: {bool(xdotool_path)}")

    # Daemon ping (avoid falling back to subprocess mode)
    if socket_path.exists():
        t0 = time.time()
        resp = daemon_request("status")
        dt_ms = int((time.time() - t0) * 1000)
        print(f"daemon status ms: {dt_ms}")
        print(f"daemon status resp: {resp}")

    recorded_file = None
    if record_test:
        if not socket_path.exists():
            print("record-test: skipped (daemon socket missing)", file=sys.stderr)
        else:
            try:
                status = daemon_request("status") or {}
                if status.get("status") == "recording":
                    print("record-test: skipped (daemon already recording)", file=sys.stderr)
                else:
                    print(f"record-test: recording for {record_seconds:.1f}s... speak now", file=sys.stderr)
                    start_resp = daemon_request("start") or {}
                    if start_resp.get("error"):
                        print(f"record-test start error: {start_resp.get('error')}", file=sys.stderr)
                    else:
                        time.sleep(max(0.1, record_seconds))
                        stop_resp = daemon_request("stop") or {}
                        recorded_file = stop_resp.get("audio_file")
                        if stop_resp.get("error"):
                            print(f"record-test stop error: {stop_resp.get('error')}", file=sys.stderr)
                        elif recorded_file and Path(recorded_file).exists():
                            size = Path(recorded_file).stat().st_size
                            print(f"record-test file: {recorded_file}")
                            print(f"record-test bytes: {size}")
                        else:
                            print("record-test: no audio file produced", file=sys.stderr)
            except Exception as e:
                print(f"record-test error: {e}", file=sys.stderr)

    if play and recorded_file and Path(recorded_file).exists():
        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            print("play: skipped (ffplay not found)", file=sys.stderr)
        else:
            try:
                play_timeout = max(5.0, float(record_seconds) + 5.0)
                print(
                    f"play: starting ffplay (timeout {play_timeout:.1f}s)...",
                    file=sys.stderr,
                )
                proc = subprocess.Popen(
                    [ffplay_path, "-autoexit", "-nodisp", "-loglevel", "error", recorded_file],
                    start_new_session=True,
                )
                try:
                    proc.wait(timeout=play_timeout)
                except subprocess.TimeoutExpired:
                    print("play: ffplay timed out, terminating...", file=sys.stderr)
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass
                except KeyboardInterrupt:
                    print("play: interrupted, terminating ffplay...", file=sys.stderr)
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    raise
            except Exception as e:
                print(f"play error: {e}", file=sys.stderr)

    if transcribe_test:
        if not recorded_file:
            print("transcribe-test: skipped (no record-test file)", file=sys.stderr)
        else:
            try:
                transcriber = WhisperTranscriber(model="whisper-1")
                text = transcriber.transcribe(recorded_file)
                print("transcribe-test text:")
                print(text)
            except Exception as e:
                print(f"transcribe-test error: {e}", file=sys.stderr)

    if audio_test:
        try:
            import numpy as np
            import sounddevice as sd

            env_device = os.environ.get("VOICEPIPE_DEVICE")
            preferred_device = int(env_device) if (env_device or "").isdigit() else None
            selection = select_audio_input(
                preferred_device_index=preferred_device,
                preferred_samplerate=_get_preferred_sample_rate(),
                preferred_channels=_get_preferred_channels(),
                strict_device_index=bool(preferred_device is not None),
            )

            fs = int(selection.samplerate)
            frames = int(0.5 * fs)
            data = sd.rec(
                frames,
                samplerate=fs,
                channels=int(selection.channels),
                dtype="int16",
                device=int(selection.device_index),
            )
            sd.wait()
            max_amp = int(np.max(np.abs(data))) if data.size else 0
            print(
                f"audio-test device={selection.device_index} samplerate={fs} "
                f"channels={selection.channels} max_amp={max_amp}"
            )
        except Exception as e:
            print(f"audio-test error: {e}", file=sys.stderr)


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
