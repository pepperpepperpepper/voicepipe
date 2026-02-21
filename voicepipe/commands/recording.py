"""Top-level recording commands (`voicepipe start|stop|status|cancel|…`)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO

import click

from voicepipe.config import get_intent_routing_enabled, get_intent_wake_prefixes, get_transcribe_model
from voicepipe.logging_utils import configure_logging
from voicepipe.paths import preserved_audio_dir
from voicepipe.intent_router import IntentResult, route_intent
from voicepipe.recording_backend import (
    AutoRecorderBackend,
    RecordingError,
)
from voicepipe.session import RecordingSession
from voicepipe.transcription import transcribe_audio_file_result, transcribe_audio_fileobj_result
from voicepipe.transcription_result import TranscriptionResult
from voicepipe.typing import type_text
from voicepipe.platform import is_windows

logger = logging.getLogger(__name__)


def _emit_transcription(
    result,
    *,
    type_: bool,
    json_output: bool,
) -> None:
    routing_enabled = get_intent_routing_enabled()
    if routing_enabled:
        intent = route_intent(result, wake_prefixes=get_intent_wake_prefixes())
    else:
        intent = IntentResult(mode="dictation", dictation_text=(result.text or "").strip(), reason="disabled")

    output_text = result.text
    if intent.mode == "dictation" and intent.dictation_text is not None:
        output_text = intent.dictation_text
    elif intent.mode == "command" and intent.command_text is not None:
        output_text = intent.command_text
    else:
        output_text = (result.text or "").strip()

    payload = result.to_dict()
    payload["intent"] = intent.to_dict()

    from voicepipe.transcript_triggers import apply_transcript_triggers

    output_text, trigger_meta = apply_transcript_triggers(output_text)
    payload["output_text"] = output_text
    if trigger_meta is not None:
        payload["transcript_trigger"] = trigger_meta

    strict_commands = os.environ.get("VOICEPIPE_COMMANDS_STRICT") == "1"
    if strict_commands and intent.mode == "command":
        if json_output:
            click.echo(json.dumps(payload, ensure_ascii=False))
        click.echo(
            "Command-mode detected but commands are not implemented yet.",
            err=True,
        )
        raise SystemExit(2)

    if json_output:
        click.echo(json.dumps(payload, ensure_ascii=False))
    else:
        click.echo(output_text)

    if type_:
        ok, err = type_text(output_text)
        if not ok:
            click.echo(f"Error typing text: {err}", err=True)


def _transcribe_and_finalize(
    *,
    audio_file: str | None,
    session: dict | None,
    recording_id: str | None,
    resolved_model: str,
    language: str | None,
    prompt: str | None,
    temperature: float,
    type_: bool,
    json_output: bool,
    keep_audio: bool,
    source: str,
    prefer_daemon: bool = True,
) -> None:
    transcription_ok = False
    try:
        if not audio_file:
            raise RuntimeError("No audio file produced")
        result = transcribe_audio_file_result(
            audio_file,
            model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            prefer_daemon=bool(prefer_daemon),
            recording_id=recording_id,
            source=source,
        )
        transcription_ok = True

        _emit_transcription(result, type_=bool(type_), json_output=bool(json_output))
    except SystemExit:
        raise
    except Exception as e:
        error_msg = str(e)
        click.echo(f"Error: {error_msg}", err=True)
        if type_:
            type_text(f"Error: {error_msg}")
        raise SystemExit(1)
    finally:
        if session:
            try:
                RecordingSession.cleanup_session(session)
            except Exception:
                pass

        if audio_file and os.path.exists(audio_file):
            if transcription_ok:
                if keep_audio:
                    click.echo(f"Kept audio file: {audio_file}", err=True)
                    return
                try:
                    os.unlink(audio_file)
                except Exception:
                    pass
            else:
                try:
                    dst_dir = preserved_audio_dir(create=True)
                    dst = dst_dir / Path(audio_file).name
                    shutil.move(audio_file, dst)
                    audio_file = str(dst)
                except Exception:
                    pass
                click.echo(f"Preserved audio file: {audio_file}", err=True)


def _transcribe_and_finalize_fileobj(
    *,
    wav_fh: BinaryIO,
    wav_filename: str,
    recording_id: str | None,
    resolved_model: str,
    language: str | None,
    prompt: str | None,
    temperature: float,
    type_: bool,
    json_output: bool,
    keep_audio: bool,
    source: str,
) -> None:
    transcription_ok = False
    try:
        result = transcribe_audio_fileobj_result(
            wav_fh,
            filename=wav_filename,
            model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            recording_id=recording_id,
            source=source,
        )
        if keep_audio:
            try:
                try:
                    wav_fh.seek(0)
                except Exception:
                    pass
                dst_dir = preserved_audio_dir(create=True)
                rid = str(recording_id or int(time.time() * 1000))
                dst = dst_dir / f"voicepipe_{rid}.wav"
                with open(dst, "wb") as out:
                    shutil.copyfileobj(wav_fh, out)
                click.echo(f"Kept audio file: {dst}", err=True)
                result = TranscriptionResult(
                    text=result.text,
                    backend=result.backend,
                    model=result.model,
                    audio_file=str(dst),
                    recording_id=result.recording_id,
                    source=result.source,
                    warnings=list(result.warnings),
                )
            except Exception:
                pass
        transcription_ok = True
        _emit_transcription(result, type_=bool(type_), json_output=bool(json_output))
    except SystemExit:
        raise
    except Exception as e:
        error_msg = str(e)
        click.echo(f"Error: {error_msg}", err=True)
        if type_:
            type_text(f"Error: {error_msg}")
        raise SystemExit(1)
    finally:
        if transcription_ok:
            return

        try:
            try:
                wav_fh.seek(0)
            except Exception:
                pass
            dst_dir = preserved_audio_dir(create=True)
            rid = str(recording_id or int(time.time() * 1000))
            dst = dst_dir / f"voicepipe_{rid}.wav"
            with open(dst, "wb") as out:
                shutil.copyfileobj(wav_fh, out)
            click.echo(f"Preserved audio file: {dst}", err=True)
        except Exception:
            pass


@click.command()
@click.option(
    "--device",
    envvar="VOICEPIPE_DEVICE",
    type=str,
    help="Audio device index or name to use (or pulse:<source>)",
)
def start(device: str | None) -> None:
    """Start recording audio from microphone."""
    try:
        backend = AutoRecorderBackend()
        result = backend.start(device=device)
        if result.mode == "daemon":
            click.echo("Recording started (daemon mode)")
        else:
            click.echo(f"Recording started (PID: {result.pid})")
    except RecordingError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command()
@click.option(
    "--type",
    "type_",
    is_flag=True,
    help="Type the transcribed text using the configured typing backend (see VOICEPIPE_TYPE_BACKEND)",
)
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
    default=None,
    help=(
        "Transcription model to use (defaults to VOICEPIPE_TRANSCRIBE_MODEL / "
        "VOICEPIPE_MODEL; backend via VOICEPIPE_TRANSCRIBE_BACKEND). "
        "You can also prefix: openai:whisper-1 or elevenlabs:scribe_v1"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
@click.option(
    "--keep-audio",
    is_flag=True,
    help="Keep the recorded audio file after transcription (prevents deletion).",
)
@click.option("--json", "json_", is_flag=True, help="Output structured JSON (default: plain text)")
def stop(
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
    keep_audio: bool,
    json_: bool,
) -> None:
    """Stop recording and transcribe the audio."""
    try:
        resolved_model = (model or get_transcribe_model()).strip()
        backend = AutoRecorderBackend()
        stop_result = backend.stop()
        _transcribe_and_finalize(
            audio_file=stop_result.audio_file,
            session=stop_result.session,
            recording_id=getattr(stop_result, "recording_id", None),
            resolved_model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            type_=bool(type_),
            json_output=bool(json_),
            keep_audio=bool(keep_audio),
            source="stop",
            prefer_daemon=True,
        )

    except RecordingError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command()
def status() -> None:
    """Check recording status."""
    try:
        backend = AutoRecorderBackend()
        result = backend.status()
        if result.mode == "daemon":
            if result.status == "recording":
                click.echo("Status: recording (daemon mode)")
            else:
                click.echo(f"Status: {result.status}")
            return

        if result.status == "recording":
            click.echo(f"Status: recording (PID: {result.pid})")
        else:
            click.echo("Status: idle")

    except RecordingError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command("transcribe-file")
@click.argument("audio_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option(
    "--type",
    "type_",
    is_flag=True,
    help="Type the transcribed text using the configured typing backend (see VOICEPIPE_TYPE_BACKEND)",
)
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
    default=None,
    help=(
        "Transcription model to use (defaults to VOICEPIPE_TRANSCRIBE_MODEL / "
        "VOICEPIPE_MODEL; backend via VOICEPIPE_TRANSCRIBE_BACKEND). "
        "You can also prefix: openai:whisper-1 or elevenlabs:scribe_v1"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
@click.option("--json", "json_", is_flag=True, help="Output structured JSON (default: plain text)")
def transcribe_file(
    type_: bool,
    audio_file: str,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
    json_: bool,
) -> None:
    """Transcribe an audio file (no recording session required)."""
    try:
        resolved_model = (model or get_transcribe_model()).strip()
        result = transcribe_audio_file_result(
            audio_file,
            model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=temperature,
            prefer_daemon=True,
            source="transcribe-file",
        )
        _emit_transcription(result, type_=bool(type_), json_output=bool(json_))

    except SystemExit:
        raise
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command("dictate")
@click.option(
    "--seconds",
    type=float,
    default=None,
    help="Record for N seconds (default: wait for ENTER on a TTY).",
)
@click.option(
    "--device",
    envvar="VOICEPIPE_DEVICE",
    type=str,
    help="Audio device index or name to use (or pulse:<source>)",
)
@click.option(
    "--type",
    "type_",
    is_flag=True,
    help="Type the transcribed text using the configured typing backend (see VOICEPIPE_TYPE_BACKEND)",
)
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
    default=None,
    help=(
        "Transcription model to use (defaults to VOICEPIPE_TRANSCRIBE_MODEL / "
        "VOICEPIPE_MODEL; backend via VOICEPIPE_TRANSCRIBE_BACKEND). "
        "You can also prefix: openai:whisper-1 or elevenlabs:scribe_v1"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
@click.option(
    "--prefer-daemon/--no-daemon",
    default=True,
    show_default=True,
    help="Prefer the daemon backend when available (falls back automatically).",
)
@click.option(
    "--keep-audio",
    is_flag=True,
    help="Keep the recorded audio file after transcription (prevents deletion).",
)
@click.option("--json", "json_", is_flag=True, help="Output structured JSON (default: plain text)")
def dictate(
    seconds: float | None,
    device: str | None,
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
    prefer_daemon: bool,
    keep_audio: bool,
    json_: bool,
) -> None:
    """Record from the mic, transcribe, and optionally type (one command)."""
    max_seconds = 300.0
    if seconds is None:
        if not sys.stdin.isatty():
            raise click.ClickException("No TTY available; pass --seconds to auto-stop")
    else:
        if float(seconds) <= 0:
            raise click.ClickException("--seconds must be > 0")
        if float(seconds) > max_seconds:
            raise click.ClickException(f"--seconds must be <= {max_seconds:.0f} (5 minutes)")

    # Keep daemon mode as the default on Unix (when enabled) for compatibility,
    # but use an in-process (in-memory) recorder when the daemon is not desired
    # or unavailable (notably on Windows).
    if not is_windows() and prefer_daemon:
        backend = AutoRecorderBackend()
        started = False
        try:
            backend.start(device=device)
            started = True

            if seconds is None:
                click.echo("Recording... press ENTER to stop (Ctrl+C to cancel).", err=True)
                _ = sys.stdin.readline()
            else:
                click.echo(f"Recording for {float(seconds):.1f}s...", err=True)
                time.sleep(float(seconds))

            stop_result = backend.stop()
            started = False
            resolved_model = (model or get_transcribe_model()).strip()
            _transcribe_and_finalize(
                audio_file=stop_result.audio_file,
                session=stop_result.session,
                recording_id=getattr(stop_result, "recording_id", None),
                resolved_model=resolved_model,
                language=language,
                prompt=prompt,
                temperature=float(temperature),
                type_=bool(type_),
                json_output=bool(json_),
                keep_audio=bool(keep_audio),
                source="dictate",
                prefer_daemon=True,
            )
            return
        except KeyboardInterrupt:
            if started:
                try:
                    backend.cancel()
                except Exception:
                    pass
            raise SystemExit(130)
        except RecordingError as e:
            if started:
                try:
                    backend.cancel()
                except Exception:
                    pass
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)
        except SystemExit:
            raise
        except Exception as e:
            if started:
                try:
                    backend.cancel()
                except Exception:
                    pass
            click.echo(f"Error: {e}", err=True)
            raise SystemExit(1)

    # In-process path: record PCM in memory and send bytes to the STT backend.
    if device is not None:
        os.environ["VOICEPIPE_DEVICE"] = str(device)

    from voicepipe.audio import resolve_audio_input_for_recording, select_audio_input, write_device_cache
    from voicepipe.config import get_audio_channels, get_audio_sample_rate
    from voicepipe.recorder import AudioRecorder
    from voicepipe.wav import write_wav_pcm

    preferred_samplerate = get_audio_sample_rate()
    preferred_channels = get_audio_channels()

    resolution = resolve_audio_input_for_recording(
        preferred_samplerate=preferred_samplerate,
        preferred_channels=preferred_channels,
    )
    selection = resolution.selection

    recorder: AudioRecorder | None = None
    try:
        try:
            recorder = AudioRecorder(
                device_index=selection.device_index,
                sample_rate=selection.samplerate,
                channels=selection.channels,
                max_duration=None,
            )
            recorder.start_recording(output_file=None)
        except Exception:
            strict = str(getattr(resolution, "source", "")).startswith("config-")
            selection = select_audio_input(
                preferred_device_index=selection.device_index,
                preferred_samplerate=preferred_samplerate,
                preferred_channels=preferred_channels,
                strict_device_index=strict,
            )
            recorder = AudioRecorder(
                device_index=selection.device_index,
                sample_rate=selection.samplerate,
                channels=selection.channels,
                max_duration=None,
            )
            recorder.start_recording(output_file=None)

            try:
                import sounddevice as sd  # type: ignore

                name = str(sd.query_devices(int(selection.device_index)).get("name", ""))
            except Exception:
                name = ""
            try:
                write_device_cache(selection, device_name=name, source="auto")
            except Exception:
                pass

        if seconds is None:
            click.echo(
                f"Recording... press ENTER to stop (Ctrl+C to cancel). (Auto-stops after {max_seconds:.0f}s)",
                err=True,
            )
            stop_event = threading.Event()

            def _wait_enter() -> None:
                try:
                    _ = sys.stdin.readline()
                finally:
                    stop_event.set()

            thread = threading.Thread(target=_wait_enter, daemon=True)
            thread.start()
            stop_event.wait(timeout=max_seconds)
            if not stop_event.is_set():
                click.echo(f"Auto-stopping after {max_seconds:.0f}s...", err=True)
        else:
            click.echo(f"Recording for {float(seconds):.1f}s...", err=True)
            time.sleep(float(seconds))

        pcm = recorder.stop_recording()
        if not pcm:
            raise click.ClickException("No audio data recorded")
    except KeyboardInterrupt:
        try:
            if recorder and recorder.recording:
                recorder.stop_recording()
        except Exception:
            pass
        try:
            if recorder:
                recorder.cleanup()
        except Exception:
            pass
        raise SystemExit(130)
    except SystemExit:
        raise
    except Exception as e:
        try:
            if recorder:
                recorder.cleanup()
        except Exception:
            pass
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    finally:
        try:
            if recorder:
                recorder.cleanup()
        except Exception:
            pass

    wav_fh = tempfile.SpooledTemporaryFile(max_size=20 * 1024 * 1024, mode="w+b")
    try:
        write_wav_pcm(
            wav_fh,
            pcm,
            sample_rate=int(getattr(recorder, "rate", selection.samplerate) or selection.samplerate),
            channels=int(getattr(recorder, "channels", selection.channels) or selection.channels),
            sample_width=2,
        )
        try:
            wav_fh.seek(0)
        except Exception:
            pass

        resolved_model = (model or get_transcribe_model()).strip()
        recording_id = str(int(time.time() * 1000))
        _transcribe_and_finalize_fileobj(
            wav_fh=wav_fh,
            wav_filename="audio.wav",
            recording_id=recording_id,
            resolved_model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            type_=bool(type_),
            json_output=bool(json_),
            keep_audio=bool(keep_audio),
            source="dictate",
        )
    finally:
        try:
            wav_fh.close()
        except Exception:
            pass


@click.command()
def cancel() -> None:
    """Cancel active recording without transcribing."""
    try:
        backend = AutoRecorderBackend()
        backend.cancel()
        click.echo("Recording cancelled")

    except RecordingError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)


@click.command()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Run the voicepipe daemon service."""
    try:
        if is_windows():
            raise click.ClickException(
                "Recorder daemon mode is not supported on Windows yet.\n\n"
                "Use:\n"
                "  voicepipe start|stop|cancel|status\n"
                "  voicepipe-fast toggle"
            )
        debug = bool((ctx.obj or {}).get("debug"))
        configure_logging(debug=debug, default_level=logging.INFO)

        from voicepipe.daemon import RecordingDaemon

        daemon = RecordingDaemon()
        daemon.start()
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
    except Exception as e:
        logger.exception("Daemon error: %s", e)
        raise SystemExit(1)
