"""`voicepipe setup` command."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from voicepipe.config import (
    ensure_env_file,
    env_file_permissions_ok,
    legacy_api_key_paths,
    read_env_file,
    upsert_env_var,
)
from voicepipe.systemd import TARGET_UNIT, install_user_units, run_systemctl, systemctl_path


def _read_key_from_stdin() -> str:
    if sys.stdin.isatty():
        raise click.UsageError("--from-stdin requires piped stdin")
    return (sys.stdin.read() or "").strip()


def _read_key_from_file(path: Path) -> str:
    try:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


@click.command("setup")
@click.option(
    "--api-key",
    help="OpenAI API key to store in the env file (unsafe in shell history).",
)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
@click.option(
    "--skip-key",
    is_flag=True,
    help="Skip writing OPENAI_API_KEY (for systemd credentials users).",
)
@click.option(
    "--systemd/--no-systemd",
    default=True,
    show_default=True,
    help="Install/enable/start systemd user services when systemctl is available.",
)
@click.option(
    "--enable/--no-enable",
    default=True,
    show_default=True,
    help="Enable voicepipe.target to start on login (systemd only).",
)
@click.option(
    "--start/--no-start",
    default=True,
    show_default=True,
    help="Start/restart voicepipe.target now (systemd only).",
)
def setup(
    api_key: str | None,
    from_stdin: bool,
    skip_key: bool,
    systemd: bool,
    enable: bool,
    start: bool,
) -> None:
    """One-command setup for config + systemd services."""

    if skip_key and (api_key or from_stdin):
        raise click.UsageError("--skip-key cannot be combined with --api-key/--from-stdin")

    env_path = ensure_env_file()
    env_values = read_env_file(env_path)
    has_key_in_file = bool((env_values.get("OPENAI_API_KEY") or "").strip())

    if not skip_key:
        chosen_key = (api_key or "").strip() if api_key else ""
        source = "--api-key"
        if from_stdin:
            chosen_key = _read_key_from_stdin()
            source = "--from-stdin"

        if chosen_key:
            upsert_env_var("OPENAI_API_KEY", chosen_key)
            has_key_in_file = True
            click.echo(f"Configured OPENAI_API_KEY in env file (source: {source})")
        elif has_key_in_file:
            click.echo("OPENAI_API_KEY already present in env file")
        else:
            shell_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
            if shell_key:
                upsert_env_var("OPENAI_API_KEY", shell_key)
                has_key_in_file = True
                click.echo("Configured OPENAI_API_KEY in env file (source: OPENAI_API_KEY env var)")
            else:
                migrated = False
                for legacy in legacy_api_key_paths():
                    legacy_key = _read_key_from_file(legacy)
                    if legacy_key:
                        upsert_env_var("OPENAI_API_KEY", legacy_key)
                        has_key_in_file = True
                        migrated = True
                        click.echo(f"Configured OPENAI_API_KEY in env file (source: legacy file: {legacy})")
                        break

                if not migrated:
                    prompted = click.prompt(
                        "OpenAI API key",
                        hide_input=True,
                        confirmation_prompt=True,
                    ).strip()
                    if not prompted:
                        raise click.ClickException("API key is empty")
                    upsert_env_var("OPENAI_API_KEY", prompted)
                    has_key_in_file = True
                    click.echo("Configured OPENAI_API_KEY in env file (source: prompt)")

    click.echo(f"env file: {env_path}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(f"env file has OPENAI_API_KEY: {has_key_in_file}")

    if systemd:
        if not systemctl_path():
            click.echo("systemctl not found; skipping systemd setup", err=True)
            return

        install_user_units()
        rc = run_systemctl(["daemon-reload"], check=False).returncode
        if enable:
            rc = max(rc, run_systemctl(["enable", TARGET_UNIT], check=False).returncode)
        if start:
            # Restart ensures config changes are picked up.
            rc = max(
                rc, run_systemctl(["restart", TARGET_UNIT], check=False).returncode
            )

        if rc != 0:
            raise SystemExit(rc)

        click.echo(f"systemd: enabled={bool(enable)} started={bool(start)} unit={TARGET_UNIT}")

