"""Command-line interface for voicepipe."""

import os
import sys
import signal
import logging
import subprocess
import time
import shutil
import threading
from pathlib import Path

import click

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

from .ipc import daemon_socket_path, send_request, try_send_request, IpcError
from .logging_utils import configure_logging
from .paths import doctor_artifacts_dir, preserved_audio_dir, runtime_app_dir
from .recorder import AudioRecorder, RecordingSession
from .transcriber import WhisperTranscriber

logger = logging.getLogger(__name__)

def run_recording_subprocess():
    """Run the actual recording in a subprocess (fallback when no daemon)."""
    recorder = None
    session = None
    timeout_timer = None
    try:
        session = RecordingSession.create_session()
        
        def _cleanup_session():
            try:
                RecordingSession.cleanup_session(session)
            except Exception:
                pass

        # Set up signal handlers
        def stop_handler(signum, frame):
            """Stop recording and save audio."""
            if timeout_timer:
                try:
                    timeout_timer.cancel()
                except Exception:
                    pass
            if recorder and recorder.recording:
                try:
                    audio_data = recorder.stop_recording()
                    if audio_data:
                        recorder.save_to_file(audio_data, session['audio_file'])
                except Exception as e:
                    print(f"Error saving audio: {e}", file=sys.stderr)
            if recorder:
                recorder.cleanup()
            _cleanup_session()
            sys.exit(0)

        def cancel_handler(signum, frame):
            """Cancel recording without saving audio."""
            if timeout_timer:
                try:
                    timeout_timer.cancel()
                except Exception:
                    pass
            if recorder and recorder.recording:
                try:
                    recorder.stop_recording()
                except Exception:
                    pass
            if recorder:
                recorder.cleanup()
            try:
                audio_file = session.get("audio_file") if session else None
                if audio_file and os.path.exists(audio_file):
                    os.unlink(audio_file)
            except Exception:
                pass
            _cleanup_session()
            sys.exit(0)

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, cancel_handler)
        try:
            signal.signal(signal.SIGUSR1, cancel_handler)
        except Exception:
            pass
        
        # Get device from environment or use default
        device = os.environ.get('VOICEPIPE_DEVICE')
        device_index = int(device) if device and device.isdigit() else None
        
        recorder = AudioRecorder(device_index=device_index, max_duration=None)
        
        print(f"Recording started (PID: {os.getpid()})...", file=sys.stderr)
        recorder.start_recording(output_file=session['audio_file'])

        # Enforce a maximum duration at the subprocess level so we can
        # gracefully stop + save, instead of the recorder timing out silently.
        def _timeout_kill():
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass

        timeout_timer = threading.Timer(300, _timeout_kill)
        timeout_timer.daemon = True
        timeout_timer.start()
        
        # Block until terminated
        signal.pause()
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if recorder:
            recorder.cleanup()
        sys.exit(1)


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx, debug):
    """Voicepipe - Voice recording and transcription CLI tool."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = bool(debug)
    if load_dotenv is not None:
        load_dotenv()
    configure_logging(debug=bool(debug), default_level=logging.WARNING)


@main.command()
@click.option(
    "--device",
    envvar="VOICEPIPE_DEVICE",
    type=int,
    help="Audio device index to use",
)
def start(device):
    """Start recording audio from microphone."""
    try:
        # Try daemon first
        response = try_send_request("start", device=device)
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
        response = None
        session = None
        audio_file = None
        transcription_ok = False

        # Try daemon first
        response = try_send_request("stop")
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
            transcription_ok = True
            
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
            # Clean up audio file only on successful transcription; otherwise preserve it.
            if audio_file and os.path.exists(audio_file):
                if transcription_ok:
                    os.unlink(audio_file)
                else:
                    try:
                        dst_dir = preserved_audio_dir(create=True)
                        dst = dst_dir / Path(audio_file).name
                        shutil.move(audio_file, dst)
                        audio_file = str(dst)
                    except Exception:
                        pass
                    print(f"Preserved audio file: {audio_file}", file=sys.stderr)
            
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
        response = try_send_request("status")
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


@main.command("doctor-legacy", hidden=True)
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
def doctor_legacy(audio_test, record_test, transcribe_test, record_seconds, play):
    """DEPRECATED: use `voicepipe doctor env|daemon|audio`."""
    print("doctor: deprecated; use `voicepipe doctor env|daemon|audio`", file=sys.stderr)
    if audio_test:
        doctor_audio()
    if record_test or transcribe_test or play:
        doctor_daemon(
            record_test=bool(record_test),
            transcribe_test=bool(transcribe_test),
            record_seconds=float(record_seconds),
            play=bool(play),
            cleanup=False,
        )


@main.group(invoke_without_command=True)
@click.pass_context
def doctor(ctx):
    """Diagnostics for common Voicepipe issues."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def _preserve_doctor_audio_file(path: Path) -> Path:
    dest_dir = doctor_artifacts_dir(create=True)
    dest = dest_dir / path.name
    if dest.exists():
        stem = path.stem
        suffix = path.suffix
        for i in range(1, 1000):
            candidate = dest_dir / f"{stem}-{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
    try:
        moved = shutil.move(str(path), str(dest))
        return Path(moved)
    except Exception:
        return path


@doctor.command("env")
def doctor_env():
    """Check environment, paths, and basic dependencies."""
    socket_path = daemon_socket_path()
    runtime_path = runtime_app_dir()

    print(f"python: {sys.executable}")
    print(f"cwd: {os.getcwd()}")
    print(f"XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '')}")
    print(f"DISPLAY: {os.environ.get('DISPLAY', '')}")
    print(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '')}")

    print(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    print(f"daemon socket exists: {socket_path} {socket_path.exists()}")

    print(f"doctor artifacts dir: {doctor_artifacts_dir()} exists: {doctor_artifacts_dir().exists()}")
    print(f"preserved audio dir: {preserved_audio_dir()} exists: {preserved_audio_dir().exists()}")

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


@doctor.command("daemon")
@click.option(
    "--record-test",
    is_flag=True,
    help="Start/stop a daemon recording and report file size",
)
@click.option(
    "--transcribe-test",
    is_flag=True,
    help="Transcribe the record-test audio file",
)
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
@click.option(
    "--cleanup",
    is_flag=True,
    help="Delete record-test output after running (default: preserve)",
)
def doctor_daemon(record_test, transcribe_test, record_seconds, play, cleanup):
    """Check daemon socket/health and (optionally) perform record/transcribe tests."""
    socket_path = daemon_socket_path()
    runtime_path = runtime_app_dir()
    print(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    print(f"daemon socket exists: {socket_path} {socket_path.exists()}")

    # Daemon ping (avoid falling back to subprocess mode)
    if socket_path.exists():
        t0 = time.time()
        try:
            resp = send_request("status", socket_path=socket_path)
        except IpcError as e:
            resp = {"error": str(e)}
        dt_ms = int((time.time() - t0) * 1000)
        print(f"daemon status ms: {dt_ms}")
        print(f"daemon status resp: {resp}")
    else:
        print("daemon status: skipped (daemon socket missing)", file=sys.stderr)

    recorded_file = None
    if record_test:
        if not socket_path.exists():
            print("record-test: skipped (daemon socket missing)", file=sys.stderr)
        else:
            try:
                status = try_send_request("status", socket_path=socket_path) or {}
                if status.get("status") == "recording":
                    print("record-test: skipped (daemon already recording)", file=sys.stderr)
                else:
                    print(
                        f"record-test: recording for {record_seconds:.1f}s... speak now",
                        file=sys.stderr,
                    )
                    start_resp = try_send_request("start", socket_path=socket_path) or {}
                    if start_resp.get("error"):
                        print(f"record-test start error: {start_resp.get('error')}", file=sys.stderr)
                    else:
                        time.sleep(max(0.1, float(record_seconds)))
                        stop_resp = try_send_request("stop", socket_path=socket_path) or {}
                        recorded_file = stop_resp.get("audio_file")
                        if stop_resp.get("error"):
                            print(f"record-test stop error: {stop_resp.get('error')}", file=sys.stderr)
                        elif recorded_file and Path(recorded_file).exists():
                            size = Path(recorded_file).stat().st_size
                            print(f"record-test file: {recorded_file}")
                            print(f"record-test bytes: {size}")
                            if cleanup:
                                print("record-test output: will delete (--cleanup)", file=sys.stderr)
                            else:
                                preserved = _preserve_doctor_audio_file(Path(recorded_file))
                                if str(preserved) != str(recorded_file):
                                    print(f"record-test preserved: {preserved}")
                                recorded_file = str(preserved)
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

    if cleanup and recorded_file and Path(recorded_file).exists():
        try:
            Path(recorded_file).unlink()
        except Exception as e:
            print(f"cleanup error: {e}", file=sys.stderr)


@doctor.command("audio")
@click.option(
    "--seconds",
    default=0.5,
    type=float,
    show_default=True,
    help="Seconds to record for microphone level test",
)
def doctor_audio(seconds: float = 0.5):
    """Record briefly and report microphone levels."""
    try:
        import numpy as np
        import sounddevice as sd
    except Exception as e:
        print(f"audio-test error: {e}", file=sys.stderr)
        return

    try:
        fs = 16000
        frames = int(max(0.01, float(seconds)) * fs)
        data = sd.rec(frames, samplerate=fs, channels=1, dtype="int16")
        sd.wait()
        max_amp = int(np.max(np.abs(data))) if data.size else 0
        print(f"audio-test max_amp: {max_amp}")
    except Exception as e:
        print(f"audio-test error: {e}", file=sys.stderr)


@main.command()
def cancel():
    """Cancel active recording without transcribing."""
    try:
        # Try daemon first
        response = try_send_request("cancel")
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
        audio_file = session.get("audio_file")
        
        # Send SIGTERM to recording process
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass  # Process already gone
        
        # Clean up session
        RecordingSession.cleanup_session(session)

        # Best-effort cleanup if the subprocess already saved something.
        if audio_file and os.path.exists(audio_file):
            try:
                os.unlink(audio_file)
            except Exception:
                pass
        
        print("Recording cancelled")
        
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


@main.command()
@click.pass_context
def daemon(ctx):
    """Run the voicepipe daemon service."""
    try:
        debug = bool((ctx.obj or {}).get("debug"))
        configure_logging(debug=debug, default_level=logging.INFO)

        from .daemon import RecordingDaemon

        daemon = RecordingDaemon()
        daemon.start()
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
    except Exception as e:
        logger.exception("Daemon error: %s", e)
        sys.exit(1)


@main.command('_record', hidden=True)
def _record():
    """Internal command to run recording subprocess."""
    run_recording_subprocess()


if __name__ == '__main__':
    main()
