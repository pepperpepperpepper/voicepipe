from __future__ import annotations

import json
import os
import sys

import click

from voicepipe.clipboard import copy_to_clipboard
from voicepipe.last_output import clear_last_output, load_last_output
from voicepipe.typing import press_enter, type_text


def _parse_default_actions(raw: str) -> set[str]:
    tokens: list[str] = []
    for part in (raw or "").replace("+", ",").split(","):
        item = (part or "").strip().lower()
        if not item:
            continue
        tokens.append(item)

    aliases = {
        "copy": "clipboard",
        "clip": "clipboard",
        "cb": "clipboard",
        "paste": "clipboard",
        "stdin": "print",
        "stdout": "print",
        "tty": "type",
    }

    out: set[str] = set()
    for item in tokens:
        out.add(aliases.get(item, item))
    return out


def _extract_execute_output_preview(payload: object) -> str | None:
    """Best-effort extract of the last `zwingli execute` output preview.

    `save_last_output()` stores an arbitrary payload dict; for Voicepipe
    transcriptions this typically includes `transcript_trigger.meta.handler_meta`.
    """
    if not isinstance(payload, dict):
        return None

    trigger = payload.get("transcript_trigger")
    if not isinstance(trigger, dict):
        return None

    meta = trigger.get("meta")
    if not isinstance(meta, dict):
        return None

    handler_meta = meta.get("handler_meta")
    if not isinstance(handler_meta, dict):
        return None

    preview = handler_meta.get("output_preview")
    if not isinstance(preview, str):
        return None

    cleaned = preview.rstrip("\n")
    return cleaned or None


def _payload_is_execute(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False

    trigger = payload.get("transcript_trigger") or payload.get("trigger_meta")
    if not isinstance(trigger, dict):
        return False

    meta = trigger.get("meta")
    if not isinstance(meta, dict):
        return False

    return str(meta.get("verb_type") or "").strip().lower() == "execute"


def _escape_multiline_for_typing(text: str) -> str:
    # Prevent accidental execution when typing multi-line output into a shell.
    # Keep it readable-ish while preserving a reversible representation.
    return text.replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


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
@click.option(
    "--execute-output",
    is_flag=True,
    help="Replay the captured output preview from the last `zwingli execute` (when available).",
)
@click.option(
    "--raw",
    is_flag=True,
    help="When used with --type and --execute-output, type raw newlines (unsafe in terminals).",
)
def replay(type_: bool, clipboard: bool, clear: bool, json_: bool, execute_output: bool, raw: bool) -> None:
    """Replay the last buffered Voicepipe output.

    By default, this prints the buffered text to stdout. Use `--type` (desktop)
    or `--clipboard` to replay it elsewhere without re-transcribing.

    \b
    Examples:
      voicepipe replay
      voicepipe replay --type
      voicepipe replay --clipboard
      voicepipe replay --json
      voicepipe replay --execute-output
    """
    if execute_output and json_:
        raise click.UsageError("--execute-output cannot be combined with --json")

    if not any([type_, clipboard, json_]):
        defaults = _parse_default_actions(os.environ.get("VOICEPIPE_REPLAY_DEFAULT") or "")
        clipboard = bool(clipboard or ("clipboard" in defaults))
        type_ = bool(type_ or ("type" in defaults))
        json_ = bool(json_ or ("json" in defaults))

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
            "  voicepipe replay --execute-output  # show captured output preview (zwingli execute)\n"
            "\n"
            "You can also set a default replay action in your config, e.g.:\n"
            "  VOICEPIPE_REPLAY_DEFAULT=clipboard\n"
        )

    replay_text = entry.text
    if execute_output:
        preview = _extract_execute_output_preview(entry.payload)
        if preview is None:
            raise click.ClickException(
                "No captured execute output preview found in replay metadata.\n\n"
                "Tip: run `voicepipe replay --json` to inspect the stored payload."
            )
        replay_text = preview
        if type_ and not raw and "\n" in replay_text:
            replay_text = _escape_multiline_for_typing(replay_text)

    if json_:
        click.echo(json.dumps(entry.to_dict(), ensure_ascii=False))
    else:
        click.echo(replay_text)

    if not json_ and not replay_text.strip():
        click.echo(
            "Note: replay buffer is empty (0 characters). Try `voicepipe replay --json` to inspect metadata.",
            err=True,
        )

    if clipboard:
        ok, err = copy_to_clipboard(replay_text)
        if not ok:
            click.echo(f"Error copying to clipboard: {err}", err=True)

    if type_:
        ok, err = type_text(replay_text)
        if not ok:
            click.echo(f"Error typing text: {err}", err=True)
        elif not execute_output and _payload_is_execute(entry.payload) and replay_text.strip():
            ok2, err2 = press_enter()
            if not ok2:
                click.echo(f"Error pressing Enter: {err2}", err=True)

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

    if (
        sys.stdout is not None
        and hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
        and not json_
        and not execute_output
    ):
        preview = _extract_execute_output_preview(entry.payload)
        if preview:
            click.echo(
                "Tip: last output includes a captured `zwingli execute` output preview; "
                "show it with `voicepipe replay --execute-output`.",
                err=True,
            )
