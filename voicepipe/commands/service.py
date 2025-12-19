"""`voicepipe service …` commands."""

from __future__ import annotations

import subprocess

import click

from voicepipe.systemd import (
    install_user_units,
    journalctl_path,
    run_systemctl,
    selected_units,
    systemctl_path,
)


@click.group(name="service")
def service_group() -> None:
    """Manage Voicepipe systemd user services."""


def _service_units(recorder: bool, transcriber: bool) -> list[str]:
    units = selected_units(recorder=bool(recorder), transcriber=bool(transcriber))
    if not units:
        raise click.ClickException("No units selected")
    return units


@service_group.command("install")
def service_install() -> None:
    """Install systemd user units into ~/.config/systemd/user/."""
    if not systemctl_path():
        raise click.ClickException("systemctl not found (is systemd installed?)")

    result = install_user_units()
    run_systemctl(["daemon-reload"], check=False)
    click.echo("Installed systemd user units:")
    click.echo(f"  {result.recorder_path}")
    click.echo(f"  {result.transcriber_path}")
    click.echo("Next:")
    click.echo("  voicepipe service enable")
    click.echo("  voicepipe service start")


@service_group.command("enable")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_enable(recorder: bool, transcriber: bool) -> None:
    """Enable Voicepipe services to start on login."""
    units = _service_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["enable", *units], check=False).returncode)


@service_group.command("disable")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_disable(recorder: bool, transcriber: bool) -> None:
    """Disable Voicepipe services."""
    units = _service_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["disable", *units], check=False).returncode)


@service_group.command("start")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_start(recorder: bool, transcriber: bool) -> None:
    """Start Voicepipe services."""
    units = _service_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["start", *units], check=False).returncode)


@service_group.command("stop")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_stop(recorder: bool, transcriber: bool) -> None:
    """Stop Voicepipe services."""
    units = _service_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["stop", *units], check=False).returncode)


@service_group.command("restart")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_restart(recorder: bool, transcriber: bool) -> None:
    """Restart Voicepipe services."""
    units = _service_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["restart", *units], check=False).returncode)


@service_group.command("status")
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_status(recorder: bool, transcriber: bool) -> None:
    """Show systemd status for Voicepipe services."""
    if not systemctl_path():
        raise click.ClickException("systemctl not found (is systemd installed?)")
    units = _service_units(recorder, transcriber)
    rc = 0
    for unit in units:
        proc = subprocess.run(
            ["systemctl", "--user", "--no-pager", "--full", "status", unit],
            check=False,
        )
        rc = max(rc, proc.returncode)
    raise SystemExit(rc)


@service_group.command("logs")
@click.option("-n", "--lines", default=200, show_default=True, help="Number of log lines")
@click.option("--follow/--no-follow", default=True, show_default=True)
@click.option("--recorder", is_flag=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, help="Only manage the transcriber unit")
def service_logs(lines: int, follow: bool, recorder: bool, transcriber: bool) -> None:
    """Tail logs for Voicepipe services."""
    if not journalctl_path():
        raise click.ClickException("journalctl not found")
    units = _service_units(recorder, transcriber)
    cmd = ["journalctl", "--user"]
    for unit in units:
        cmd.extend(["-u", unit])
    cmd.extend(["-n", str(int(lines))])
    if follow:
        cmd.append("-f")
    raise SystemExit(subprocess.run(cmd, check=False).returncode)

