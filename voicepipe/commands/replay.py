from __future__ import annotations

import json
import sys

import click

from voicepipe.clipboard import copy_to_clipboard
from voicepipe.last_output import clear_last_output, load_last_output
from voicepipe.typing import type_text


@click.command("replay")
@click.option(
    "-t",
    "--type",
    "type_",
    is_flag=True,
    help="Type the last buffered output using the configured typing backend.",
)
@click.option(
    "-c",
    "--clipboard",
    is_flag=True,
    help="Copy the last buffered output to your clipboard.",
)
@click.option("-x", "--clear", is_flag=True, help="Clear the buffered output after replaying.")
@click.option(
    "-j",
    "--json",
    "json_",
    is_flag=True,
    help="Output structured JSON (includes metadata).",
)
def replay(type_: bool, clipboard: bool, clear: bool, json_: bool) -> None:
    """Replay the last buffered Voicepipe output.

    By default, this prints the buffered text to stdout. Use `--type` (desktop)
    or `--clipboard` to replay it elsewhere without re-transcribing.

    \b
    Examples:
      voicepipe replay
      voicepipe replay --type
      voicepipe replay --clipboard
      voicepipe replay --json
    """
    entry = load_last_output()
    if entry is None:
        raise click.ClickException(
            "No buffered output found yet.\n\n"
            "Record/transcribe something first, e.g.:\n"
            "  voicepipe dictate --seconds 3\n\n"
            "Then replay it with:\n"
            "  voicepipe replay            # print\n"
            "  voicepipe replay --type     # type into focused window\n"
            "  voicepipe replay --clipboard  # copy to clipboard\n"
            "  voicepipe replay --json     # show metadata\n"
        )

    if json_:
        click.echo(json.dumps(entry.to_dict(), ensure_ascii=False))
    else:
        click.echo(entry.text)

    if not json_ and not entry.text.strip():
        click.echo(
            "Note: replay buffer is empty (0 characters). Try `voicepipe replay --json` to inspect metadata.",
            err=True,
        )

    if clipboard:
        ok, err = copy_to_clipboard(entry.text)
        if not ok:
            click.echo(f"Error copying to clipboard: {err}", err=True)

    if type_:
        ok, err = type_text(entry.text)
        if not ok:
            click.echo(f"Error typing text: {err}", err=True)

    if clear:
        clear_last_output()

    if (
        sys.stdout is not None
        and hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and not any([type_, clipboard, clear, json_])
    ):
        click.echo(
            "Tip: use `voicepipe replay --type` to type it again, or `voicepipe replay --clipboard` to copy it.",
            err=True,
        )
