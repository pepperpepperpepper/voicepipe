from __future__ import annotations

import click

from voicepipe.platform import is_windows
from voicepipe.systemd import TARGET_UNIT, systemctl_path


def print_restart_hint() -> None:
    if is_windows():
        click.echo(
            "Restart Voicepipe to pick up changes (re-run your hotkey app / restart your Task Scheduler task)."
        )
        return

    if systemctl_path():
        click.echo(
            "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
            "  voicepipe service restart\n"
            f"  # or: systemctl --user restart {TARGET_UNIT}"
        )
        return

    click.echo("Restart Voicepipe to pick up changes.")

