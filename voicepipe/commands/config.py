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
    detect_groq_api_key,
    detect_openai_api_key,
    ensure_env_file,
    ensure_settings_file,
    env_file_path,
    env_file_permissions_ok,
    get_intent_routing_enabled,
    get_intent_wake_prefixes,
    get_error_reporting_enabled,
    get_transcribe_backend,
    get_transcribe_model,
    get_zwingli_backend,
    get_zwingli_base_url,
    get_zwingli_model,
    get_zwingli_user_prompt,
    get_zwingli_temperature,
    legacy_api_key_paths,
    legacy_elevenlabs_key_paths,
    read_settings_file,
    read_env_file,
    settings_file_path,
    upsert_env_var,
)
from voicepipe.systemd import TARGET_UNIT


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
    """Store the OpenAI API key in ~/.config/voicepipe/voicepipe.env."""
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
    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )


@config_group.command("set-elevenlabs-key")
@click.argument("api_key", required=False)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
def config_set_elevenlabs_key(api_key: str | None, from_stdin: bool) -> None:
    """Store the ElevenLabs API key in ~/.config/voicepipe/voicepipe.env."""
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
    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )


@config_group.command("set-groq-key")
@click.argument("api_key", required=False)
@click.option(
    "--from-stdin",
    is_flag=True,
    help="Read the API key from stdin (avoids shell history).",
)
def config_set_groq_key(api_key: str | None, from_stdin: bool) -> None:
    """Store the Groq API key in ~/.config/voicepipe/voicepipe.env."""
    if from_stdin:
        if sys.stdin.isatty():
            raise click.UsageError("--from-stdin requires piped stdin")
        api_key = (sys.stdin.read() or "").strip()

    if not api_key:
        api_key = click.prompt(
            "Groq API key",
            hide_input=True,
            confirmation_prompt=True,
        ).strip()

    if not api_key:
        raise click.ClickException("API key is empty")

    env_path = upsert_env_var("GROQ_API_KEY", api_key)
    ok = env_file_permissions_ok(env_path)
    click.echo(f"Wrote GROQ_API_KEY to: {env_path}")
    if ok is False:
        click.echo(
            f"Warning: expected permissions 0600 but got different mode on: {env_path}",
            err=True,
        )
    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )


@config_group.command("show")
def config_show() -> None:
    """Show which config sources are present (never prints secrets)."""
    env_path = env_file_path()
    env_values = read_env_file(env_path)
    settings_path = settings_file_path()
    settings_values = read_settings_file(settings_path)

    key_env = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    groq_env = bool((os.environ.get("GROQ_API_KEY") or "").strip())
    key_env_file = bool((env_values.get("OPENAI_API_KEY") or "").strip())
    groq_env_file = bool((env_values.get("GROQ_API_KEY") or "").strip())
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
    click.echo(f"env var GROQ_API_KEY set: {groq_env}")
    click.echo(f"env var ELEVENLABS_API_KEY/XI_API_KEY set: {eleven_env}")
    click.echo(f"env file exists: {env_path} {env_path.exists()}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(f"env file has OPENAI_API_KEY: {key_env_file}")
    click.echo(f"env file has GROQ_API_KEY: {groq_env_file}")
    click.echo(f"env file has ELEVENLABS_API_KEY/XI_API_KEY: {eleven_env_file}")
    click.echo(f"settings file exists: {settings_path} {settings_path.exists()}")
    click.echo(f"systemd credentials available: {creds_dir}")

    for path in legacy_api_key_paths():
        click.echo(f"legacy key file exists: {path} {path.exists()}")
    for path in legacy_elevenlabs_key_paths():
        click.echo(f"legacy elevenlabs key file exists: {path} {path.exists()}")

    click.echo(f"api key resolvable: {detect_openai_api_key()}")
    click.echo(f"groq api key resolvable: {detect_groq_api_key()}")
    click.echo(f"elevenlabs api key resolvable: {detect_elevenlabs_api_key()}")
    click.echo(f"transcribe backend resolved: {get_transcribe_backend()}")
    click.echo(f"transcribe model resolved: {get_transcribe_model()}")
    click.echo(
        f"device env set (VOICEPIPE_DEVICE): {bool(os.environ.get('VOICEPIPE_DEVICE'))}"
    )
    prefixes = get_intent_wake_prefixes()
    click.echo(f"intent routing enabled: {get_intent_routing_enabled()}")
    click.echo(
        "intent wake prefixes resolved: "
        + (", ".join(prefixes) if prefixes else "(none)")
    )
    click.echo(f"error reporting enabled: {get_error_reporting_enabled()}")
    click.echo(f"zwingli backend resolved: {get_zwingli_backend()}")
    base_url = get_zwingli_base_url()
    click.echo(f"zwingli base url resolved: {base_url if base_url else '(default)'}")
    click.echo(f"zwingli model resolved: {get_zwingli_model()}")
    click.echo(f"zwingli temperature resolved: {get_zwingli_temperature()}")
    click.echo(f"zwingli user prompt set: {bool(get_zwingli_user_prompt().strip())}")
    intent_settings = settings_values.get("intent") if isinstance(settings_values, dict) else None
    if isinstance(intent_settings, dict):
        click.echo(
            f"settings intent.routing_enabled: {intent_settings.get('routing_enabled')}"
        )
        click.echo(
            "settings intent.wake_prefixes: "
            + (
                ", ".join(str(x) for x in intent_settings.get("wake_prefixes") or [])
                or "(unset)"
            )
        )

    zwingli_settings = settings_values.get("zwingli") if isinstance(settings_values, dict) else None
    if isinstance(zwingli_settings, dict):
        click.echo(f"settings zwingli.backend: {zwingli_settings.get('backend')}")
        click.echo(f"settings zwingli.base_url: {zwingli_settings.get('base_url')}")
        click.echo(f"settings zwingli.model: {zwingli_settings.get('model')}")


@config_group.command("edit")
def config_edit() -> None:
    """Edit the canonical env file in $EDITOR (never prints secrets)."""
    env_path = ensure_env_file()

    editor = (os.environ.get("EDITOR") or "").strip()
    if not editor:
        for candidate in ("nano", "vim", "vi"):
            path = shutil.which(candidate)
            if path:
                editor = path
                break

    if not editor:
        raise click.ClickException("No editor found (set $EDITOR)")

    cmd = [*shlex.split(editor), str(env_path)]
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise SystemExit(rc)

    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )


@config_group.command("edit-settings")
def config_edit_settings() -> None:
    """Edit the canonical settings TOML in $EDITOR."""
    settings_path = ensure_settings_file()

    editor = (os.environ.get("EDITOR") or "").strip()
    if not editor:
        for candidate in ("nano", "vim", "vi"):
            path = shutil.which(candidate)
            if path:
                editor = path
                break

    if not editor:
        raise click.ClickException("No editor found (set $EDITOR)")

    cmd = [*shlex.split(editor), str(settings_path)]
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise SystemExit(rc)

    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )


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
    click.echo(
        "If you're using the systemd services, restart Voicepipe to pick up changes:\n"
        "  voicepipe service restart\n"
        f"  # or: systemctl --user restart {TARGET_UNIT}"
    )
