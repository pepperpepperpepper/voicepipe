"""`voicepipe config …` commands."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import click

from voicepipe.audio_device import (
    get_default_pulse_source,
    list_pulse_sources,
    resolve_device_index,
)
from voicepipe.config import (
    detect_elevenlabs_api_key,
    detect_openai_api_key,
    ensure_env_file,
    env_file_path,
    env_file_permissions_ok,
    get_audio_channels,
    get_audio_sample_rate,
    load_environment,
    get_transcribe_backend,
    get_transcribe_model,
    get_audio_channels,
    get_audio_sample_rate,
    legacy_api_key_paths,
    legacy_elevenlabs_key_paths,
    read_env_file,
    triggers_json_path,
    upsert_env_var,
)
from voicepipe.systemd import (
    RECORDER_UNIT,
    TARGET_UNIT,
    run_systemctl,
    systemctl_path,
    systemctl_show_properties,
)
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
    trig_path = triggers_json_path()
    click.echo(f"triggers.json path: {trig_path} {trig_path.exists()}")

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
    click.echo(
        f"pulse source env set (VOICEPIPE_PULSE_SOURCE): {bool(os.environ.get('VOICEPIPE_PULSE_SOURCE'))}"
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

    def _split_editor_command(raw: str) -> list[str]:
        if is_windows():
            # On Windows, `shlex.split()` with POSIX rules treats backslashes as
            # escape characters. Use `posix=False` and strip wrapping quotes.
            parts = shlex.split(raw, posix=False)
            cleaned: list[str] = []
            for part in parts:
                if len(part) >= 2 and part[0] == part[-1] and part[0] in ('"', "'"):
                    cleaned.append(part[1:-1])
                else:
                    cleaned.append(part)
            return cleaned
        return shlex.split(raw)

    cmd = [*_split_editor_command(editor), str(env_path)]
    try:
        rc = subprocess.run(cmd, check=False).returncode
    except (FileNotFoundError, OSError):
        # Windows fallback: let Explorer choose an editor.
        if is_windows():
            try:
                os.startfile(env_path)  # type: ignore[attr-defined]
                print_restart_hint()
                return
            except Exception as e:
                raise click.ClickException(f"Failed to open env file: {e}") from e
        raise
    if rc != 0:
        raise SystemExit(rc)
    print_restart_hint()


def _probe_device_level(
    *,
    device_index: int,
    seconds: float,
    samplerate: int,
    channels: int,
) -> int:
    import numpy as np
    import sounddevice as sd

    frames = int(max(0.05, float(seconds)) * float(samplerate))
    data = sd.rec(
        frames,
        samplerate=int(samplerate),
        channels=int(channels),
        dtype="int16",
        device=int(device_index),
    )
    sd.wait()
    return int(np.max(np.abs(data))) if data.size else 0


def _probe_pulse_source(
    *,
    source: str,
    seconds: float,
    samplerate: int,
    channels: int,
) -> int:
    # Prefer `arecord` on Linux when available. It tracks Pulse/PipeWire sources
    # reliably even on systems where PortAudio/sounddevice capture returns
    # silent buffers (all zeros).
    if not is_windows():
        arecord = shutil.which("arecord")
        if arecord:
            try:
                import numpy as np

                samples = int(max(1, float(seconds) * float(samplerate)))
                env = os.environ.copy()
                env["PULSE_SOURCE"] = source
                proc = subprocess.run(
                    [
                        arecord,
                        "-q",
                        "-D",
                        "pulse",
                        "-f",
                        "S16_LE",
                        "-r",
                        str(int(samplerate)),
                        "-c",
                        str(int(channels)),
                        "-t",
                        "raw",
                        "-s",
                        str(int(samples)),
                    ],
                    capture_output=True,
                    env=env,
                    check=False,
                    timeout=max(2.0, float(seconds) + 2.0),
                )
                if proc.returncode == 0:
                    data = proc.stdout or b""
                    arr = np.frombuffer(data, dtype=np.int16)
                    return int(np.max(np.abs(arr.astype(np.int32)))) if arr.size else 0
            except Exception:
                pass

    # Fallback: PortAudio via sounddevice.
    prev = os.environ.get("PULSE_SOURCE")
    os.environ["PULSE_SOURCE"] = source
    try:
        idx, err = resolve_device_index("pulse")
        if err or idx is None:
            raise RuntimeError(err or "pulse device not found")
        return _probe_device_level(
            device_index=idx,
            seconds=seconds,
            samplerate=samplerate,
            channels=channels,
        )
    finally:
        if prev is None:
            os.environ.pop("PULSE_SOURCE", None)
        else:
            os.environ["PULSE_SOURCE"] = prev


def _format_source_line(idx: int, name: str, description: str, level: int | None) -> str:
    desc = f" - {description}" if description else ""
    level_text = f" (level={level})" if level is not None else ""
    return f"{idx:>2}. {name}{desc}{level_text}"


def _write_legacy_device_files(*, device_value: str, pulse_source: str | None) -> None:
    """Persist device selection for legacy configs (best-effort)."""
    try:
        cfg_dir = Path.home() / ".config" / "voicepipe"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        device_path = cfg_dir / "device"
        device_path.write_text(device_value + "\n", encoding="utf-8")
        if pulse_source:
            pulse_path = cfg_dir / "pulse_source"
            pulse_path.write_text(pulse_source + "\n", encoding="utf-8")
    except Exception:
        pass


def _recorder_is_active() -> bool:
    if not systemctl_path():
        return False
    try:
        props = systemctl_show_properties(RECORDER_UNIT, ["ActiveState"])
        return props.get("ActiveState") == "active"
    except Exception:
        return False


def _stop_recorder_if_active() -> bool:
    if not _recorder_is_active():
        return False
    try:
        run_systemctl(["stop", RECORDER_UNIT], check=False)
        return True
    except Exception:
        return False


def _restart_recorder_if_needed(was_active: bool) -> None:
    if not was_active or not systemctl_path():
        return
    try:
        run_systemctl(["start", RECORDER_UNIT], check=False)
    except Exception:
        pass


@config_group.command("audio")
@click.option(
    "--seconds",
    default=5.0,
    type=float,
    show_default=True,
    help="Seconds to sample each input during auto-detection",
)
@click.option(
    "--auto/--wizard",
    default=None,
    help="Auto-detect without prompts (default: wizard on TTY)",
)
@click.option(
    "--list",
    "list_only",
    is_flag=True,
    help="List detected inputs and exit",
)
def config_audio(seconds: float, auto: bool | None, list_only: bool) -> None:
    """Detect and configure the preferred audio input."""
    load_environment()
    seconds = float(seconds)
    recorder_was_active = False

    sources = list_pulse_sources()
    non_monitor = [s for s in sources if not s.is_monitor]
    if sources and not non_monitor:
        non_monitor = sources

    if non_monitor:
        if list_only:
            click.echo("PulseAudio sources:")
            for i, src in enumerate(non_monitor, start=1):
                click.echo(_format_source_line(i, src.name, src.description, None))
            return

        use_wizard = auto is False or (auto is None and sys.stdin.isatty())
        levels: list[int] = []
        if use_wizard:
            click.echo(
                "Testing PulseAudio sources. Please speak so we can pick the loudest mic..."
            )

        for src in non_monitor:
            try:
                level = _probe_pulse_source(
                    source=src.name,
                    seconds=seconds,
                    samplerate=get_audio_sample_rate(),
                    channels=get_audio_channels(),
                )
            except Exception as e:
                if use_wizard:
                    click.echo(f"  {src.name}: error: {e}", err=True)
                level = 0
            levels.append(level)

        silence_threshold = 50
        max_level = max(levels) if levels else 0
        if max_level <= silence_threshold:
            click.echo(
                "PulseAudio sources returned silence; falling back to ALSA device scan.",
                err=True,
            )
            # Try the PipeWire device (often works even when Pulse source probing fails).
            try:
                idx, err = resolve_device_index("pipewire")
                if err is None and idx is not None:
                    level = _probe_device_level(
                        device_index=int(idx),
                        seconds=seconds,
                        samplerate=get_audio_sample_rate(),
                        channels=get_audio_channels(),
                    )
                    if level > silence_threshold:
                        device_value = "pipewire"
                        env_path = upsert_env_var("VOICEPIPE_DEVICE", device_value)
                        _write_legacy_device_files(
                            device_value=device_value,
                            pulse_source=None,
                        )
                        click.echo("Configured VOICEPIPE_DEVICE=pipewire")
                        click.echo(f"env file: {env_path}")
                        try:
                            from voicepipe.audio import select_audio_input, write_device_cache
                            import sounddevice as sd

                            selection = select_audio_input(
                                preferred_device_index=int(idx),
                                preferred_samplerate=get_audio_sample_rate(),
                                preferred_channels=get_audio_channels(),
                                strict_device_index=True,
                            )
                            write_device_cache(
                                selection=selection,
                                device_name=str(sd.query_devices(int(idx)).get("name", "")),
                                source="manual",
                            )
                        except Exception:
                            pass
                        print_restart_hint()
                        _restart_recorder_if_needed(recorder_was_active)
                        return
            except Exception:
                pass
            recorder_was_active = _stop_recorder_if_active()
        else:
            if use_wizard:
                click.echo("Detected sources:")
                for i, (src, level) in enumerate(zip(non_monitor, levels), start=1):
                    click.echo(_format_source_line(i, src.name, src.description, level))

                default_idx = 1
                if levels:
                    default_idx = int(max(range(len(levels)), key=lambda i: levels[i])) + 1
                choice = click.prompt(
                    "Select input",
                    default=default_idx,
                    type=click.IntRange(1, len(non_monitor)),
                )
                chosen = non_monitor[int(choice) - 1]
            else:
                default_source = get_default_pulse_source()
                chosen = None
                if default_source:
                    for src in non_monitor:
                        if src.name == default_source:
                            chosen = src
                            break
                if chosen is None:
                    loudest_idx = int(max(range(len(levels)), key=lambda i: levels[i]))
                    chosen = non_monitor[loudest_idx]

            device_value = f"pulse:{chosen.name}"
            env_path = upsert_env_var("VOICEPIPE_DEVICE", device_value)
            upsert_env_var("VOICEPIPE_PULSE_SOURCE", chosen.name)
            _write_legacy_device_files(
                device_value=device_value,
                pulse_source=chosen.name,
            )
            click.echo(f"Configured VOICEPIPE_DEVICE=pulse:{chosen.name}")
            click.echo(f"Configured VOICEPIPE_PULSE_SOURCE={chosen.name}")
            click.echo(f"env file: {env_path}")
            # Best-effort: also update the auto-detect cache.
            try:
                import sounddevice as sd
                from voicepipe.audio import select_audio_input, write_device_cache

                idx, err = resolve_device_index(device_value)
                if err is None and idx is not None:
                    selection = select_audio_input(
                        preferred_device_index=int(idx),
                        preferred_samplerate=get_audio_sample_rate(),
                        preferred_channels=get_audio_channels(),
                        strict_device_index=True,
                    )
                    write_device_cache(
                        selection=selection,
                        device_name=str(sd.query_devices(int(idx)).get("name", "")),
                        source="manual",
                    )
            except Exception:
                pass
            print_restart_hint()
            return
        # If PulseAudio sources are silent, fall through to ALSA scan below.

    # Fallback: use sounddevice device list.
    try:
        import sounddevice as sd
    except Exception as e:
        _restart_recorder_if_needed(recorder_was_active)
        raise click.ClickException(f"sounddevice not available: {e}") from e

    inputs: list[tuple[int, str]] = []
    try:
        devices = sd.query_devices()
    except Exception as e:
        _restart_recorder_if_needed(recorder_was_active)
        raise click.ClickException(f"Failed to list audio devices: {e}") from e
    for idx, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = str(dev.get("name", ""))
        inputs.append((idx, name))

    if not inputs:
        _restart_recorder_if_needed(recorder_was_active)
        raise click.ClickException("No input devices found")

    if list_only:
        click.echo("Input devices:")
        for i, (idx, name) in enumerate(inputs, start=1):
            click.echo(_format_source_line(i, f"{idx} - {name}", "", None))
        _restart_recorder_if_needed(recorder_was_active)
        return

    use_wizard = auto is False or (auto is None and sys.stdin.isatty())
    if use_wizard:
        click.echo("Testing input devices. Please speak so we can pick the loudest mic...")
        levels = []
        for idx, _name in inputs:
            try:
                level = _probe_device_level(
                    device_index=idx,
                    seconds=seconds,
                    samplerate=get_audio_sample_rate(),
                    channels=get_audio_channels(),
                )
            except Exception as e:
                click.echo(f"  device {idx}: error: {e}", err=True)
                level = 0
            levels.append(level)

        click.echo("Detected input devices:")
        for i, ((idx, name), level) in enumerate(zip(inputs, levels), start=1):
            click.echo(_format_source_line(i, f"{idx} - {name}", "", level))

        default_idx = 1
        if levels:
            default_idx = int(max(range(len(levels)), key=lambda i: levels[i])) + 1
        choice = click.prompt(
            "Select input",
            default=default_idx,
            type=click.IntRange(1, len(inputs)),
        )
        chosen_idx = inputs[int(choice) - 1][0]
    else:
        default_in = None
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = None
        chosen_idx = int(default_in) if isinstance(default_in, int) else inputs[0][0]

    device_value = str(chosen_idx)
    env_path = upsert_env_var("VOICEPIPE_DEVICE", device_value)
    _write_legacy_device_files(device_value=device_value, pulse_source=None)
    click.echo(f"Configured VOICEPIPE_DEVICE={chosen_idx}")
    click.echo(f"env file: {env_path}")
    # Best-effort: also update the auto-detect cache.
    try:
        from voicepipe.audio import select_audio_input, write_device_cache

        selection = select_audio_input(
            preferred_device_index=int(chosen_idx),
            preferred_samplerate=get_audio_sample_rate(),
            preferred_channels=get_audio_channels(),
            strict_device_index=True,
        )
        write_device_cache(
            selection=selection,
            device_name=str(sd.query_devices(int(chosen_idx)).get("name", "")),
            source="manual",
        )
    except Exception:
        pass
    print_restart_hint()
    _restart_recorder_if_needed(recorder_was_active)


@config_group.command("migrate")
@click.option(
    "--delete-legacy",
    is_flag=True,
    help="Delete legacy key files after migrating (dangerous).",
)
def config_migrate(delete_legacy: bool) -> None:
    """Migrate legacy key locations into the canonical Voicepipe env file."""
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
