"""Top-level recording commands (`voicepipe start|stop|status|cancel|…`)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import click

from voicepipe.config import get_transcribe_model
from voicepipe.logging_utils import configure_logging
from voicepipe.paths import preserved_audio_dir
from voicepipe.intent_router import route_intent
from voicepipe.recording_backend import (
    AutoRecorderBackend,
    RecordingError,
    SubprocessRecorderBackend,
)
from voicepipe.session import RecordingSession
from voicepipe.transcription import transcribe_audio_file_result
from voicepipe.typing import type_text

logger = logging.getLogger(__name__)


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

        intent = route_intent(result)
        output_text = result.text
        if intent.mode == "dictation" and intent.dictation_text is not None:
            output_text = intent.dictation_text
        elif intent.mode == "command" and intent.command_text is not None:
            output_text = intent.command_text

        payload = result.to_dict()
        payload["intent"] = intent.to_dict()

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


@click.command()
@click.option(
    "--device",
    envvar="VOICEPIPE_DEVICE",
    type=int,
    help="Audio device index to use",
)
def start(device: int | None) -> None:
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
@click.option("--type", "type_", is_flag=True, help="Type the transcribed text using xdotool")
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
def stop(
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
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
@click.option("--type", "type_", is_flag=True, help="Type the transcribed text using xdotool")
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
        intent = route_intent(result)
        output_text = result.text
        if intent.mode == "dictation" and intent.dictation_text is not None:
            output_text = intent.dictation_text
        elif intent.mode == "command" and intent.command_text is not None:
            output_text = intent.command_text

        payload = result.to_dict()
        payload["intent"] = intent.to_dict()

        strict_commands = os.environ.get("VOICEPIPE_COMMANDS_STRICT") == "1"
        if strict_commands and intent.mode == "command":
            if json_:
                click.echo(json.dumps(payload, ensure_ascii=False))
            click.echo(
                "Command-mode detected but commands are not implemented yet.",
                err=True,
            )
            raise SystemExit(2)

        if json_:
            click.echo(json.dumps(payload, ensure_ascii=False))
        else:
            click.echo(output_text)

        if type_:
            ok, err = type_text(output_text)
            if not ok:
                click.echo(f"Error typing text: {err}", err=True)

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
    type=int,
    help="Audio device index to use",
)
@click.option("--type", "type_", is_flag=True, help="Type the transcribed text using xdotool")
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
@click.option("--json", "json_", is_flag=True, help="Output structured JSON (default: plain text)")
def dictate(
    seconds: float | None,
    device: int | None,
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
    prefer_daemon: bool,
    json_: bool,
) -> None:
    """Record from the mic, transcribe, and optionally type (one command)."""
    if seconds is None:
        if not sys.stdin.isatty():
            raise click.ClickException("No TTY available; pass --seconds to auto-stop")
    else:
        if float(seconds) <= 0:
            raise click.ClickException("--seconds must be > 0")

    backend = AutoRecorderBackend() if prefer_daemon else SubprocessRecorderBackend()
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
            source="dictate",
            prefer_daemon=True,
        )
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
