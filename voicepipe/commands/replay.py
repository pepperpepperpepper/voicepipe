from __future__ import annotations

import json

import click

from voicepipe.clipboard import copy_to_clipboard
from voicepipe.last_output import clear_last_output, load_last_output
from voicepipe.typing import type_text


@click.command("replay")
@click.option(
    "--type",
    "type_",
    is_flag=True,
    help="Type the last buffered output using the configured typing backend.",
)
@click.option("--clipboard", is_flag=True, help="Copy the last buffered output to your clipboard.")
@click.option("--clear", is_flag=True, help="Clear the buffered output after replaying.")
@click.option("--json", "json_", is_flag=True, help="Output structured JSON (default: plain text)")
def replay(type_: bool, clipboard: bool, clear: bool, json_: bool) -> None:
    """Replay the last output text without re-transcribing."""
    entry = load_last_output()
    if entry is None:
        raise click.ClickException("No buffered output found yet.")

    if json_:
        click.echo(json.dumps(entry.to_dict(), ensure_ascii=False))
    else:
        click.echo(entry.text)

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

