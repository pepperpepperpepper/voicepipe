"""Top-level recording commands (`voicepipe start|stop|status|cancel|…`)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import click

from voicepipe.config import get_transcribe_model
from voicepipe.logging_utils import configure_logging
from voicepipe.paths import preserved_audio_dir
from voicepipe.recording_backend import AutoRecorderBackend, RecordingError
from voicepipe.session import RecordingSession
from voicepipe.transcription import transcribe_audio_file
from voicepipe.typing import type_text

logger = logging.getLogger(__name__)


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
        "VOICEPIPE_MODEL or gpt-4o-transcribe)"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
def stop(
    type_: bool,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
) -> None:
    """Stop recording and transcribe the audio."""
    try:
        resolved_model = (model or get_transcribe_model()).strip()
        backend = AutoRecorderBackend()
        stop_result = backend.stop()

        session = stop_result.session
        audio_file = stop_result.audio_file
        transcription_ok = False

        try:
            text = transcribe_audio_file(
                audio_file,
                model=resolved_model,
                language=language,
                prompt=prompt,
                temperature=temperature,
                prefer_daemon=True,
            )
            transcription_ok = True

            # Output to stdout
            click.echo(text)

            if type_:
                ok, err = type_text(text)
                if not ok:
                    click.echo(f"Error typing text: {err}", err=True)

        except Exception as e:
            error_msg = str(e)
            click.echo(f"Error: {error_msg}", err=True)
            if type_:
                type_text(f"Error: {error_msg}")
            raise SystemExit(1)
        finally:
            # Clean up session (only for subprocess mode).
            if session:
                RecordingSession.cleanup_session(session)

            # Clean up audio file only on successful transcription; otherwise preserve.
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
                    click.echo(f"Preserved audio file: {audio_file}", err=True)

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
        "VOICEPIPE_MODEL or gpt-4o-transcribe)"
    ),
)
@click.option(
    "--temperature",
    default=0.0,
    type=float,
    help="Temperature for transcription (0.0 for deterministic, default: 0.0)",
)
def transcribe_file(
    type_: bool,
    audio_file: str,
    language: str | None,
    prompt: str | None,
    model: str | None,
    temperature: float,
) -> None:
    """Transcribe an audio file (no recording session required)."""
    try:
        resolved_model = (model or get_transcribe_model()).strip()
        text = transcribe_audio_file(
            audio_file,
            model=resolved_model,
            language=language,
            prompt=prompt,
            temperature=temperature,
            prefer_daemon=True,
        )
        click.echo(text)

        if type_:
            ok, err = type_text(text)
            if not ok:
                click.echo(f"Error typing text: {err}", err=True)

    except SystemExit:
        raise
    except Exception as e:
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
