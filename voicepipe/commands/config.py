"""`voicepipe config …` commands."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

import click

from voicepipe.config import (
    detect_elevenlabs_api_key,
    detect_openai_api_key,
    ensure_env_file,
    env_file_path,
    env_file_permissions_ok,
    get_transcribe_backend,
    get_transcribe_model,
    legacy_api_key_paths,
    legacy_elevenlabs_key_paths,
    read_env_file,
    upsert_env_var,
)
from voicepipe.systemd import TARGET_UNIT
from voicepipe.platform import is_windows
from voicepipe.commands._hints import print_restart_hint


@click.group(name="config")
def config_group() -> None:
    """Manage Voicepipe configuration."""


@config_group.command("set-openai-key")
@click.argument("api_key", required=False)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
def config_set_openai_key(api_key: str | None, from_stdin: bool) -> None:
    """Store the OpenAI API key in the Voicepipe env file."""
    if from_stdin:
        if sys.stdin.isatty():
            raise click.UsageError("--from-stdin requires piped stdin")
        api_key = (sys.stdin.read() or "").strip()

    if not api_key:
        api_key = click.prompt(
            "OpenAI API key",
            hide_input=True,
            confirmation_prompt=True,
        ).strip()

    if not api_key:
        raise click.ClickException("API key is empty")

    env_path = upsert_env_var("OPENAI_API_KEY", api_key)
    ok = env_file_permissions_ok(env_path)
    click.echo(f"Wrote OPENAI_API_KEY to: {env_path}")
    if ok is False:
        click.echo(
            f"Warning: expected permissions 0600 but got different mode on: {env_path}",
            err=True,
        )
    print_restart_hint()


@config_group.command("set-elevenlabs-key")
@click.argument("api_key", required=False)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
def config_set_elevenlabs_key(api_key: str | None, from_stdin: bool) -> None:
    """Store the ElevenLabs API key in the Voicepipe env file."""
    if from_stdin:
        if sys.stdin.isatty():
            raise click.UsageError("--from-stdin requires piped stdin")
        api_key = (sys.stdin.read() or "").strip()

    if not api_key:
        api_key = click.prompt(
            "ElevenLabs API key",
            hide_input=True,
            confirmation_prompt=True,
        ).strip()

    if not api_key:
        raise click.ClickException("API key is empty")

    env_path = upsert_env_var("ELEVENLABS_API_KEY", api_key)
    ok = env_file_permissions_ok(env_path)
    click.echo(f"Wrote ELEVENLABS_API_KEY to: {env_path}")
    if ok is False:
        click.echo(
            f"Warning: expected permissions 0600 but got different mode on: {env_path}",
            err=True,
        )
    print_restart_hint()


@config_group.command("show")
def config_show() -> None:
    """Show which config sources are present (never prints secrets)."""
    env_path = env_file_path()
    env_values = read_env_file(env_path)

    key_env = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    key_env_file = bool((env_values.get("OPENAI_API_KEY") or "").strip())
    eleven_env = bool(
        (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
        or (os.environ.get("XI_API_KEY") or "").strip()
    )
    eleven_env_file = bool(
        (env_values.get("ELEVENLABS_API_KEY") or "").strip()
        or (env_values.get("XI_API_KEY") or "").strip()
    )
    creds_dir = bool((os.environ.get("CREDENTIALS_DIRECTORY") or "").strip())

    click.echo(f"env var OPENAI_API_KEY set: {key_env}")
    click.echo(f"env var ELEVENLABS_API_KEY/XI_API_KEY set: {eleven_env}")
    click.echo(f"env file exists: {env_path} {env_path.exists()}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(f"env file has OPENAI_API_KEY: {key_env_file}")
    click.echo(f"env file has ELEVENLABS_API_KEY/XI_API_KEY: {eleven_env_file}")
    click.echo(f"systemd credentials available: {creds_dir}")

    for path in legacy_api_key_paths():
        click.echo(f"legacy key file exists: {path} {path.exists()}")
    for path in legacy_elevenlabs_key_paths():
        click.echo(f"legacy elevenlabs key file exists: {path} {path.exists()}")

    click.echo(f"api key resolvable: {detect_openai_api_key()}")
    click.echo(f"elevenlabs api key resolvable: {detect_elevenlabs_api_key()}")
    click.echo(f"transcribe backend resolved: {get_transcribe_backend()}")
    click.echo(f"transcribe model resolved: {get_transcribe_model()}")
    click.echo(
        f"device env set (VOICEPIPE_DEVICE): {bool(os.environ.get('VOICEPIPE_DEVICE'))}"
    )


@config_group.command("edit")
def config_edit() -> None:
    """Edit the canonical env file in $EDITOR (never prints secrets)."""
    env_path = ensure_env_file()

    editor = (os.environ.get("EDITOR") or "").strip()
    if not editor:
        if is_windows():
            editor = "notepad"
        else:
            for candidate in ("nano", "vim", "vi"):
                path = shutil.which(candidate)
                if path:
                    editor = path
                    break

    if not editor:
        raise click.ClickException("No editor found (set $EDITOR)")

    cmd = [*shlex.split(editor), str(env_path)]
    try:
        rc = subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        # Windows fallback: let Explorer choose an editor.
        if is_windows():
            try:
                os.startfile(env_path)  # type: ignore[attr-defined]
                return
            except Exception as e:
                raise click.ClickException(f"Failed to open env file: {e}") from e
        raise
    if rc != 0:
        raise SystemExit(rc)

    print_restart_hint()


@config_group.command("migrate")
@click.option(
    "--delete-legacy",
    is_flag=True,
    help="Delete legacy key files after migrating (dangerous).",
)
def config_migrate(delete_legacy: bool) -> None:
    """Migrate legacy key locations into ~/.config/voicepipe/voicepipe.env."""
    env_path = env_file_path()
    env_values = read_env_file(env_path)
    if (env_values.get("OPENAI_API_KEY") or "").strip():
        click.echo(f"env file already contains OPENAI_API_KEY: {env_path}")
        return

    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    source = "OPENAI_API_KEY env var"

    if not key:
        for legacy in legacy_api_key_paths():
            try:
                if legacy.exists():
                    key = legacy.read_text(encoding="utf-8").strip()
                    if key:
                        source = f"legacy file: {legacy}"
                        if delete_legacy:
                            try:
                                legacy.unlink()
                            except Exception as e:
                                click.echo(
                                    f"Warning: failed to delete {legacy}: {e}", err=True
                                )
                        break
            except Exception:
                continue

    if not key:
        raise click.ClickException("No legacy key found to migrate")

    upsert_env_var("OPENAI_API_KEY", key)
    click.echo(f"Migrated OPENAI_API_KEY from {source} to: {env_path}")
    print_restart_hint()
