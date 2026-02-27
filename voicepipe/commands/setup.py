"""`voicepipe setup` command."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

from voicepipe.config import (
    ensure_env_file,
    ensure_triggers_json,
    env_file_permissions_ok,
    legacy_api_key_paths,
    legacy_elevenlabs_key_paths,
    read_env_file,
    upsert_env_var,
)
from voicepipe.platform import is_macos, is_windows
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


def _enable_execute_in_triggers_json(path: Path) -> bool:
    """Best-effort: ensure the `execute` and `subprocess` verbs are enabled."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig") or "")
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False

    verbs = payload.get("verbs")
    if verbs is None:
        verbs = {}
        payload["verbs"] = verbs
    if not isinstance(verbs, dict):
        return False

    changed = False

    existing = verbs.get("execute")
    if isinstance(existing, dict):
        if str(existing.get("type") or "").strip().lower() != "execute":
            existing["type"] = "execute"
            changed = True
        if existing.get("enabled") is not True:
            existing["enabled"] = True
            changed = True
        if "timeout_seconds" not in existing:
            existing["timeout_seconds"] = 10
            changed = True
    else:
        verbs["execute"] = {
            "type": "execute",
            "enabled": True,
            "timeout_seconds": 10,
        }
        changed = True

    existing = verbs.get("subprocess")
    if isinstance(existing, dict):
        if str(existing.get("type") or "").strip().lower() != "shell":
            existing["type"] = "shell"
            changed = True
        if existing.get("enabled") is not True:
            existing["enabled"] = True
            changed = True
        if "timeout_seconds" not in existing:
            existing["timeout_seconds"] = 10
            changed = True
    else:
        verbs["subprocess"] = {
            "type": "shell",
            "enabled": True,
            "timeout_seconds": 10,
        }
        changed = True

    if not changed:
        return False

    try:
        rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        path.write_text(rendered, encoding="utf-8")
        return True
    except Exception:
        return False


@click.command("setup")
@click.option(
    "--backend",
    type=click.Choice(["openai", "elevenlabs"], case_sensitive=False),
    default=None,
    help="Transcription backend to configure (defaults to VOICEPIPE_TRANSCRIBE_BACKEND or openai).",
)
@click.option(
    "--api-key",
    help="API key to store in the env file (unsafe in shell history).",
)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
@click.option(
    "--skip-key",
    is_flag=True,
    help="Skip writing the API key (for systemd credentials users).",
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
    backend: str | None,
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
    selected_backend = (
        (backend or "").strip()
        or (env_values.get("VOICEPIPE_TRANSCRIBE_BACKEND") or "").strip()
        or (os.environ.get("VOICEPIPE_TRANSCRIBE_BACKEND") or "").strip()
        or "openai"
    ).lower()
    if selected_backend in {"xi", "eleven", "eleven-labs"}:
        selected_backend = "elevenlabs"

    if backend:
        upsert_env_var("VOICEPIPE_TRANSCRIBE_BACKEND", selected_backend)

    if selected_backend == "elevenlabs":
        prompt_label = "ElevenLabs API key"
        canonical_key_env = "ELEVENLABS_API_KEY"
        env_names = ("ELEVENLABS_API_KEY", "XI_API_KEY")
        legacy_paths = legacy_elevenlabs_key_paths()
        has_key_in_file = bool(
            (env_values.get("ELEVENLABS_API_KEY") or "").strip()
            or (env_values.get("XI_API_KEY") or "").strip()
        )
    else:
        prompt_label = "OpenAI API key"
        canonical_key_env = "OPENAI_API_KEY"
        env_names = ("OPENAI_API_KEY",)
        legacy_paths = legacy_api_key_paths()
        has_key_in_file = bool((env_values.get("OPENAI_API_KEY") or "").strip())

    if not skip_key:
        chosen_key = (api_key or "").strip() if api_key else ""
        source = "--api-key"
        if from_stdin:
            chosen_key = _read_key_from_stdin()
            source = "--from-stdin"

        if chosen_key:
            upsert_env_var(canonical_key_env, chosen_key)
            has_key_in_file = True
            click.echo(f"Configured {canonical_key_env} in env file (source: {source})")
        elif has_key_in_file:
            click.echo(f"{canonical_key_env} already present in env file")
        else:
            shell_key = ""
            for name in env_names:
                candidate = (os.environ.get(name) or "").strip()
                if candidate:
                    shell_key = candidate
                    break
            if shell_key:
                upsert_env_var(canonical_key_env, shell_key)
                has_key_in_file = True
                click.echo(
                    f"Configured {canonical_key_env} in env file (source: {env_names[0]} env var)"
                )
            else:
                migrated = False
                for legacy in legacy_paths:
                    legacy_key = _read_key_from_file(legacy)
                    if legacy_key:
                        upsert_env_var(canonical_key_env, legacy_key)
                        has_key_in_file = True
                        migrated = True
                        click.echo(
                            f"Configured {canonical_key_env} in env file (source: legacy file: {legacy})"
                        )
                        break

                if not migrated:
                    prompted = click.prompt(
                        prompt_label,
                        hide_input=True,
                        confirmation_prompt=True,
                    ).strip()
                    if not prompted:
                        raise click.ClickException("API key is empty")
                    upsert_env_var(canonical_key_env, prompted)
                    has_key_in_file = True
                    click.echo(
                        f"Configured {canonical_key_env} in env file (source: prompt)"
                    )

    click.echo(f"env file: {env_path}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(f"transcribe backend: {selected_backend}")
    if selected_backend == "elevenlabs":
        click.echo(f"env file has ELEVENLABS_API_KEY/XI_API_KEY: {has_key_in_file}")
    else:
        click.echo(f"env file has OPENAI_API_KEY: {has_key_in_file}")

    shell_allow_in_file = (env_values.get("VOICEPIPE_SHELL_ALLOW") or "").strip()
    if not shell_allow_in_file:
        from_env = (os.environ.get("VOICEPIPE_SHELL_ALLOW") or "").strip()
        if from_env:
            upsert_env_var("VOICEPIPE_SHELL_ALLOW", from_env)
            click.echo("Configured VOICEPIPE_SHELL_ALLOW in env file (source: env var)")
        else:
            upsert_env_var("VOICEPIPE_SHELL_ALLOW", "1")
            click.echo(
                "Configured VOICEPIPE_SHELL_ALLOW=1 (enables `zwingli subprocess` command execution)."
            )
            click.echo(
                "Warning: this allows running shell commands from transcribed speech. "
                "Disable by setting VOICEPIPE_SHELL_ALLOW=0.",
                err=True,
            )

    triggers_path = ensure_triggers_json()
    click.echo(f"triggers config: {triggers_path}")
    if _enable_execute_in_triggers_json(triggers_path):
        click.echo("Enabled `execute` and `subprocess` verbs in triggers.json.")

    if systemd:
        if is_windows():
            click.echo(
                "systemd is not available on Windows; skipping systemd setup.\n"
                "Use Task Scheduler or the Startup folder to run Voicepipe at login.",
                err=True,
            )
            return

        if is_macos():
            click.echo(
                "systemd is not available on macOS; skipping systemd setup.\n"
                "Use launchd LaunchAgents (or a Shortcuts/Automator workflow) to run Voicepipe at login.\n"
                "Tip: `voicepipe launchd install` can generate a LaunchAgent plist (opt-in).",
                err=True,
            )
            return

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
