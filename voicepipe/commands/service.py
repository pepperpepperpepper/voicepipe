"""`voicepipe service …` commands."""

from __future__ import annotations

import os
import subprocess

import click

from voicepipe.config import ensure_env_file, read_env_file
from voicepipe.platform import is_macos, is_windows
from voicepipe.systemd import (
    RECORDER_UNIT,
    TARGET_UNIT,
    TRANSCRIBER_UNIT,
    install_user_units,
    journalctl_path,
    run_systemctl,
    selected_units,
    systemctl_cat,
    systemctl_show_properties,
    systemctl_path,
    user_unit_dir,
)


@click.group(name="service")
def service_group() -> None:
    """Manage Voicepipe systemd user services."""
    if is_windows():
        raise click.ClickException(
            "systemd is not available on Windows.\n\n"
            "Use Task Scheduler or the Startup folder to run Voicepipe at login."
        )
    if is_macos():
        raise click.ClickException(
            "systemd is not available on macOS.\n\n"
            "Use launchd LaunchAgents (or a Shortcuts/Automator workflow) to run Voicepipe at login.\n"
            "Tip: `voicepipe launchd install` can generate a LaunchAgent plist (opt-in)."
        )


def _service_units(recorder: bool, transcriber: bool) -> list[str]:
    units = selected_units(recorder=bool(recorder), transcriber=bool(transcriber))
    if not units:
        raise click.ClickException("No units selected")
    return units


def _activation_units(recorder: bool, transcriber: bool) -> list[str]:
    if recorder or transcriber:
        return _service_units(recorder, transcriber)
    try:
        if systemctl_cat(TARGET_UNIT).returncode == 0:
            return [TARGET_UNIT]
    except Exception:
        pass
    return selected_units()


@service_group.command("install")
def service_install() -> None:
    """Install systemd user units into ~/.config/systemd/user/."""
    if not systemctl_path():
        raise click.ClickException("systemctl not found (is systemd installed?)")

    result = install_user_units()
    run_systemctl(["daemon-reload"], check=False)
    env_path = ensure_env_file()
    env_values = read_env_file(env_path)
    backend_raw = (
        env_values.get("VOICEPIPE_TRANSCRIBE_BACKEND")
        or env_values.get("VOICEPIPE_BACKEND")
        or "openai"
    )
    backend = str(backend_raw).strip().lower()
    if backend in {"xi", "eleven", "eleven-labs"}:
        backend = "elevenlabs"

    has_openai_key = bool((env_values.get("OPENAI_API_KEY") or "").strip())
    has_eleven_key = bool(
        (env_values.get("ELEVENLABS_API_KEY") or "").strip()
        or (env_values.get("XI_API_KEY") or "").strip()
    )
    has_key = has_openai_key if backend != "elevenlabs" else has_eleven_key

    click.echo("Installed systemd user units:")
    click.echo(f"  {result.recorder_path}")
    click.echo(f"  {result.transcriber_path}")
    click.echo(f"  {result.target_path}")
    click.echo("Next:")
    click.echo(f"  systemctl --user enable {TARGET_UNIT}")
    click.echo(f"  systemctl --user start {TARGET_UNIT}")
    click.echo("Or:")
    click.echo("  voicepipe service enable")
    click.echo("  voicepipe service start")
    click.echo("Config:")
    click.echo(f"  env file: {env_path}")
    if not has_key:
        if backend == "elevenlabs":
            click.echo(
                "  ElevenLabs API key not set (systemd won’t see your .bashrc exports)."
            )
            if (os.environ.get("ELEVENLABS_API_KEY") or "").strip() or (
                os.environ.get("XI_API_KEY") or ""
            ).strip():
                click.echo(
                    "  Detected ELEVENLABS_API_KEY/XI_API_KEY in this shell; store it with:"
                )
                click.echo(
                    "    echo '...' | voicepipe config set-elevenlabs-key --from-stdin"
                )
            else:
                click.echo("  Set it with (recommended, avoids shell history):")
                click.echo(
                    "    echo '...' | voicepipe config set-elevenlabs-key --from-stdin"
                )
            click.echo("  Or run:")
            click.echo("    voicepipe setup --backend elevenlabs")
        else:
            click.echo("  OPENAI_API_KEY not set (systemd won’t see your .bashrc exports).")
            if (os.environ.get("OPENAI_API_KEY") or "").strip():
                click.echo("  Detected OPENAI_API_KEY in this shell; migrate it with:")
                click.echo("    voicepipe config migrate")
            else:
                click.echo("  Set it with (recommended, avoids shell history):")
                click.echo(
                    "    echo 'sk-...' | voicepipe config set-openai-key --from-stdin"
                )
            click.echo("  Or run:")
            click.echo("    voicepipe setup")


@service_group.command("uninstall")
def service_uninstall() -> None:
    """Uninstall systemd user units from ~/.config/systemd/user/."""
    if not systemctl_path():
        raise click.ClickException("systemctl not found (is systemd installed?)")

    # Stop/disable first to clean up wants/links.
    run_systemctl(["disable", "--now", TARGET_UNIT], check=False)
    run_systemctl(["disable", "--now", RECORDER_UNIT, TRANSCRIBER_UNIT], check=False)

    unit_dir = user_unit_dir()
    removed: list[str] = []
    for unit in (RECORDER_UNIT, TRANSCRIBER_UNIT, TARGET_UNIT):
        path = unit_dir / unit
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception as e:
            raise click.ClickException(f"failed to remove {path}: {e}") from e

    run_systemctl(["daemon-reload"], check=False)

    if removed:
        click.echo("Removed systemd user units:")
        for path in removed:
            click.echo(f"  {path}")
    else:
        click.echo("No Voicepipe systemd user unit files found to remove.")


@service_group.command("enable")
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_enable(recorder: bool, transcriber: bool) -> None:
    """Enable Voicepipe services to start on login."""
    units = _activation_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["enable", *units], check=False).returncode)


@service_group.command("disable")
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_disable(recorder: bool, transcriber: bool) -> None:
    """Disable Voicepipe services."""
    if recorder or transcriber:
        units = _service_units(recorder, transcriber)
        raise SystemExit(run_systemctl(["disable", *units], check=False).returncode)

    # When disabling everything, be backward compatible with older installs that
    # don't have the target unit yet.
    rc_target = 0
    try:
        if systemctl_cat(TARGET_UNIT).returncode == 0:
            rc_target = run_systemctl(["disable", TARGET_UNIT], check=False).returncode
    except Exception:
        rc_target = 0
    rc_services = run_systemctl(
        ["disable", RECORDER_UNIT, TRANSCRIBER_UNIT], check=False
    ).returncode
    raise SystemExit(max(rc_target, rc_services))


@service_group.command("start")
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_start(recorder: bool, transcriber: bool) -> None:
    """Start Voicepipe services."""
    units = _activation_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["start", *units], check=False).returncode)


@service_group.command("stop")
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_stop(recorder: bool, transcriber: bool) -> None:
    """Stop Voicepipe services."""
    units = _activation_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["stop", *units], check=False).returncode)


@service_group.command("restart")
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_restart(recorder: bool, transcriber: bool) -> None:
    """Restart Voicepipe services."""
    units = _activation_units(recorder, transcriber)
    raise SystemExit(run_systemctl(["restart", *units], check=False).returncode)


def _format_unit_line(unit: str) -> tuple[str, bool, bool]:
    props_wanted = ["LoadState", "ActiveState", "SubState", "UnitFileState"]
    props = systemctl_show_properties(unit, props_wanted)
    load_state = props.get("LoadState", "")
    active_state = props.get("ActiveState", "") or "unknown"
    sub_state = props.get("SubState", "")
    unit_file_state = props.get("UnitFileState", "")
    err = props.get("error", "")

    if load_state == "not-found":
        return f"{unit}: not found", False, False

    if err and not load_state:
        return f"{unit}: not found", False, False

    active_detail = f"{active_state} ({sub_state})" if sub_state else active_state
    enabled_detail = unit_file_state or "unknown"
    is_active = active_state == "active"
    return f"{unit}: {active_detail} [{enabled_detail}]", True, is_active


@service_group.command("status")
@click.option(
    "--full",
    is_flag=True,
    help="Show full `systemctl --user status ...` output (verbose).",
)
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
def service_status(full: bool, recorder: bool, transcriber: bool) -> None:
    """Show systemd status for Voicepipe services."""
    if not systemctl_path():
        raise click.ClickException("systemctl not found (is systemd installed?)")

    if bool(full):
        if recorder or transcriber:
            units = _service_units(recorder, transcriber)
        else:
            units = [RECORDER_UNIT, TRANSCRIBER_UNIT]
            try:
                if systemctl_cat(TARGET_UNIT).returncode == 0:
                    units = [TARGET_UNIT, *units]
            except Exception:
                pass
        rc = 0
        for unit in units:
            proc = subprocess.run(
                ["systemctl", "--user", "--no-pager", "--full", "status", unit],
                check=False,
            )
            rc = max(rc, proc.returncode)
        raise SystemExit(rc)

    click.echo("Voicepipe services (systemd --user):")
    units: list[str] = []
    if recorder or transcriber:
        units = _service_units(recorder, transcriber)
    else:
        try:
            if systemctl_cat(TARGET_UNIT).returncode == 0:
                units.append(TARGET_UNIT)
        except Exception:
            pass
        units.extend([RECORDER_UNIT, TRANSCRIBER_UNIT])

    any_missing = False
    any_inactive = False
    for unit in units:
        line, found, is_active = _format_unit_line(unit)
        click.echo(f"  {line}")
        if not found:
            any_missing = True
        elif not is_active:
            any_inactive = True

    if any_missing:
        click.echo("")
        click.echo("Not installed? Run:")
        click.echo("  voicepipe service install")
        click.echo("  voicepipe service enable")
        click.echo("  voicepipe service start")
        return

    if any_inactive:
        click.echo("")
        click.echo("Not running? Try:")
        click.echo("  voicepipe service restart")
        click.echo(f"  # or: systemctl --user restart {TARGET_UNIT}")
        return


@service_group.command("logs")
@click.option("-n", "--lines", default=200, show_default=True, help="Number of log lines")
@click.option("--follow/--no-follow", default=True, show_default=True)
@click.option("--recorder", is_flag=True, hidden=True, help="Only manage the recorder unit")
@click.option("--transcriber", is_flag=True, hidden=True, help="Only manage the transcriber unit")
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
