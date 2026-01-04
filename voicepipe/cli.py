"""Command-line interface for voicepipe."""

from __future__ import annotations

import logging

import click

from voicepipe.commands import register
from voicepipe.config import load_environment
from voicepipe.logging_utils import configure_logging


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.pass_context
def main(ctx: click.Context, debug: bool) -> None:
    """Voicepipe - Voice recording and transcription CLI tool."""
    ctx.ensure_object(dict)
    ctx.obj["debug"] = bool(debug)
    load_environment()
    configure_logging(debug=bool(debug), default_level=logging.WARNING)

register(main)


@main.command("_record", hidden=True)
def _record() -> None:
    """Internal command to run recording subprocess."""
    from voicepipe.recording_subprocess import run_recording_subprocess

    run_recording_subprocess()


if __name__ == "__main__":
    main()
