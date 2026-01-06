"""`voicepipe launchd …` commands (macOS only).

This provides an opt-in helper for generating/installing LaunchAgents. Voicepipe
does not enable launchd services by default.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from voicepipe.paths import logs_dir
from voicepipe.platform import is_macos


ServiceName = click.Choice(["transcriber", "recorder"], case_sensitive=False)


@dataclass(frozen=True)
class LaunchdService:
    service: str
    label: str
    program_arguments: list[str]
    stdout_path: Path
    stderr_path: Path


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _default_label(service: str) -> str:
    if service == "recorder":
        return "com.voicepipe.recorder"
    return "com.voicepipe.transcriber"


def _service_definition(*, service: str, label: str, python: str) -> LaunchdService:
    log_dir = logs_dir(create=False)
    if service == "recorder":
        argv = [python, "-m", "voicepipe.cli", "daemon"]
        stdout = log_dir / "voicepipe-recorder-daemon.log"
        stderr = log_dir / "voicepipe-recorder-daemon.log"
        return LaunchdService(
            service=service,
            label=label,
            program_arguments=argv,
            stdout_path=stdout,
            stderr_path=stderr,
        )

    argv = [python, "-m", "voicepipe.transcriber_daemon"]
    stdout = log_dir / "voicepipe-transcriber-daemon.log"
    stderr = log_dir / "voicepipe-transcriber-daemon.log"
    return LaunchdService(
        service=service,
        label=label,
        program_arguments=argv,
        stdout_path=stdout,
        stderr_path=stderr,
    )


def _render_plist(service: LaunchdService) -> bytes:
    payload: dict[str, object] = {
        "Label": service.label,
        "ProgramArguments": service.program_arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path.home()),
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "PYTHONUNBUFFERED": "1",
        },
        "StandardOutPath": str(service.stdout_path),
        "StandardErrorPath": str(service.stderr_path),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _plist_path(label: str) -> Path:
    return _launch_agents_dir() / f"{label}.plist"


def _run_launchctl(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _launchctl_domain_target() -> str:
    # Use the GUI domain to tie the job to the user's graphical login session.
    # NOTE: `os.getuid()` is not available on Windows. We keep this function
    # resilient so unit tests can exercise the macOS code paths on any host.
    try:
        getuid = getattr(os, "getuid", None)
        if callable(getuid):
            return f"gui/{int(getuid())}"
    except Exception:
        pass
    return "gui/0"


@click.group(name="launchd", invoke_without_command=True)
@click.pass_context
def launchd_group(ctx: click.Context) -> None:
    """Manage Voicepipe launchd LaunchAgents (macOS only)."""
    if not is_macos():
        raise click.ClickException("launchd helpers are only available on macOS.")
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@launchd_group.command("print")
@click.option("--service", "service_name", type=ServiceName, default="transcriber")
@click.option("--label", default=None, help="LaunchAgent label (default: com.voicepipe.<service>)")
@click.option("--python", "python_path", default=None, help="Python executable to use (default: current)")
def launchd_print(service_name: str, label: str | None, python_path: str | None) -> None:
    """Print a LaunchAgent plist to stdout."""
    python = (python_path or sys.executable).strip() or sys.executable
    resolved_label = (label or "").strip() or _default_label(service_name)
    service = _service_definition(service=service_name, label=resolved_label, python=python)
    click.echo(_render_plist(service).decode("utf-8"))


@launchd_group.command("install")
@click.option("--service", "service_name", type=ServiceName, default="transcriber")
@click.option("--label", default=None, help="LaunchAgent label (default: com.voicepipe.<service>)")
@click.option("--python", "python_path", default=None, help="Python executable to use (default: current)")
@click.option("--force", is_flag=True, help="Overwrite existing plist if present")
@click.option(
    "--load/--no-load",
    default=False,
    show_default=True,
    help="Load the LaunchAgent immediately (opt-in; requires a GUI login session)",
)
def launchd_install(
    service_name: str,
    label: str | None,
    python_path: str | None,
    force: bool,
    load: bool,
) -> None:
    """Install a LaunchAgent plist under ~/Library/LaunchAgents (does not load by default)."""
    python = (python_path or sys.executable).strip() or sys.executable
    resolved_label = (label or "").strip() or _default_label(service_name)
    plist_path = _plist_path(resolved_label)

    if plist_path.exists() and not force:
        raise click.ClickException(f"plist already exists: {plist_path} (use --force to overwrite)")

    # Ensure parent dirs exist.
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    logs_dir(create=True)

    service = _service_definition(service=service_name, label=resolved_label, python=python)
    plist_path.write_bytes(_render_plist(service))

    click.echo(f"Wrote LaunchAgent plist: {plist_path}")
    click.echo(f"Service: {service_name} label={resolved_label}")

    if not load:
        click.echo("Not loaded (default). To load:")
        click.echo(f"  launchctl bootstrap {_launchctl_domain_target()} {plist_path}")
        click.echo(f"  launchctl kickstart -k {_launchctl_domain_target()}/{resolved_label}")
        return

    domain = _launchctl_domain_target()

    proc = _run_launchctl(["bootstrap", domain, str(plist_path)])
    if proc.returncode != 0:
        # Fall back to legacy load for older environments.
        proc = _run_launchctl(["load", "-w", str(plist_path)])
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise click.ClickException(detail or f"launchctl failed (rc={proc.returncode})")

    _run_launchctl(["kickstart", "-k", f"{domain}/{resolved_label}"])
    click.echo("Loaded.")


@launchd_group.command("uninstall")
@click.option("--service", "service_name", type=ServiceName, default="transcriber")
@click.option("--label", default=None, help="LaunchAgent label (default: com.voicepipe.<service>)")
@click.option(
    "--unload/--no-unload",
    default=True,
    show_default=True,
    help="Unload the LaunchAgent before removing the plist (best-effort)",
)
def launchd_uninstall(service_name: str, label: str | None, unload: bool) -> None:
    """Unload (optional) and remove the LaunchAgent plist."""
    resolved_label = (label or "").strip() or _default_label(service_name)
    plist_path = _plist_path(resolved_label)

    if unload:
        domain = _launchctl_domain_target()
        proc = _run_launchctl(["bootout", domain, str(plist_path)])
        if proc.returncode != 0:
            # Best-effort fallback; ignore failures.
            _run_launchctl(["unload", "-w", str(plist_path)])

    if plist_path.exists():
        plist_path.unlink()
        click.echo(f"Removed: {plist_path}")
    else:
        click.echo(f"No plist found at: {plist_path}")
