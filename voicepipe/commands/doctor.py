"""`voicepipe doctor …` commands."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import click

from voicepipe.config import (
    detect_elevenlabs_api_key,
    detect_openai_api_key,
    env_file_path,
    env_file_permissions_ok,
    legacy_api_key_paths,
    legacy_elevenlabs_key_paths,
    read_env_file,
)
from voicepipe.ipc import IpcError, send_request, try_send_request
from voicepipe.paths import (
    daemon_socket_paths,
    doctor_artifacts_dir,
    find_daemon_socket_path,
    find_transcriber_socket_path,
    preserved_audio_dir,
    runtime_app_dir,
    transcriber_socket_paths,
    logs_dir,
    state_dir,
)
from voicepipe.systemd import (
    RECORDER_UNIT,
    TARGET_UNIT,
    TRANSCRIBER_UNIT,
    systemctl_cat,
    systemctl_path,
    systemctl_show_properties,
)
from voicepipe.typing import resolve_typing_backend
from voicepipe.platform import is_macos, is_windows


@click.group(name="doctor", invoke_without_command=True)
@click.pass_context
def doctor_group(ctx: click.Context) -> None:
    """Diagnostics for common Voicepipe issues."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def _preserve_doctor_audio_file(path: Path) -> Path:
    dest_dir = doctor_artifacts_dir(create=True)
    dest = dest_dir / path.name
    if dest.exists():
        stem = path.stem
        suffix = path.suffix
        for i in range(1, 1000):
            candidate = dest_dir / f"{stem}-{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
    try:
        moved = shutil.move(str(path), str(dest))
        return Path(moved)
    except Exception:
        return path


@doctor_group.command("env")
def doctor_env() -> None:
    """Check environment, paths, and basic dependencies."""
    daemon_socket = find_daemon_socket_path()
    transcriber_socket = find_transcriber_socket_path()
    runtime_path = runtime_app_dir()

    click.echo(f"python: {sys.executable}")
    click.echo(f"cwd: {os.getcwd()}")
    click.echo(f"platform: {sys.platform}")

    if is_windows():
        click.echo(f"USERPROFILE: {os.environ.get('USERPROFILE', '')}")
        click.echo(f"APPDATA: {os.environ.get('APPDATA', '')}")
        click.echo(f"LOCALAPPDATA: {os.environ.get('LOCALAPPDATA', '')}")
        click.echo(f"TEMP: {os.environ.get('TEMP', '')}")
        click.echo(f"TMP: {os.environ.get('TMP', '')}")
    elif is_macos():
        click.echo(f"HOME: {os.environ.get('HOME', '')}")
        click.echo(f"TMPDIR: {os.environ.get('TMPDIR', '')}")

    click.echo(f"XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '')}")
    click.echo(f"XDG_SESSION_TYPE: {os.environ.get('XDG_SESSION_TYPE', '')}")
    click.echo(f"XDG_CURRENT_DESKTOP: {os.environ.get('XDG_CURRENT_DESKTOP', '')}")
    click.echo(f"DISPLAY: {os.environ.get('DISPLAY', '')}")
    click.echo(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '')}")
    click.echo(f"VOICEPIPE_TYPE_BACKEND: {os.environ.get('VOICEPIPE_TYPE_BACKEND', '')}")
    click.echo(f"VOICEPIPE_DAEMON_MODE: {os.environ.get('VOICEPIPE_DAEMON_MODE', '')}")

    env = dict(os.environ)
    resolved = resolve_typing_backend(env=env)
    auto_env = dict(env)
    auto_env.pop("VOICEPIPE_TYPE_BACKEND", None)
    auto = resolve_typing_backend(env=auto_env)
    click.echo(
        f"typing backend resolved: {resolved.name} "
        f"(session={resolved.session_type}, supports_window_id={resolved.supports_window_id})"
    )
    click.echo(f"typing backend reason: {resolved.reason}")
    if resolved.path:
        click.echo(f"typing backend path: {resolved.path}")
    if resolved.error:
        click.echo(f"typing backend error: {resolved.error}")
    click.echo(
        f"typing backend auto would choose: {auto.name} "
        f"(session={auto.session_type}, supports_window_id={auto.supports_window_id})"
    )
    click.echo(f"typing backend auto reason: {auto.reason}")

    click.echo(f"env file path: {env_file_path()}")
    click.echo(f"state dir: {state_dir()} exists: {state_dir().exists()}")
    click.echo(f"logs dir: {logs_dir()} exists: {logs_dir().exists()}")
    click.echo(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    click.echo(f"daemon socket: {daemon_socket or '(not found)'}")
    click.echo(f"daemon socket candidates: {', '.join(str(p) for p in daemon_socket_paths())}")
    click.echo(f"transcriber socket: {transcriber_socket or '(not found)'}")
    click.echo(
        f"transcriber socket candidates: {', '.join(str(p) for p in transcriber_socket_paths())}"
    )

    click.echo(
        f"doctor artifacts dir: {doctor_artifacts_dir()} exists: {doctor_artifacts_dir().exists()}"
    )
    click.echo(
        f"preserved audio dir: {preserved_audio_dir()} exists: {preserved_audio_dir().exists()}"
    )

    # API key presence (never print the key)
    key_env = os.environ.get("OPENAI_API_KEY")
    key_eleven_env = (os.environ.get("ELEVENLABS_API_KEY") or "") or (
        os.environ.get("XI_API_KEY") or ""
    )
    key_env_file = env_file_path()
    click.echo(f"OPENAI_API_KEY env set: {bool(key_env)}")
    click.echo(f"ELEVENLABS_API_KEY/XI_API_KEY env set: {bool(key_eleven_env)}")
    click.echo(f"env file exists: {key_env_file} {key_env_file.exists()}")
    for path in legacy_api_key_paths():
        click.echo(f"legacy key file exists: {path} {path.exists()}")
    for path in legacy_elevenlabs_key_paths():
        click.echo(f"legacy elevenlabs key file exists: {path} {path.exists()}")
    click.echo(f"api key resolvable: {detect_openai_api_key()}")
    click.echo(f"elevenlabs api key resolvable: {detect_elevenlabs_api_key()}")

    ffmpeg_path = shutil.which("ffmpeg")
    xdotool_path = shutil.which("xdotool")
    wtype_path = shutil.which("wtype")
    click.echo(f"ffmpeg found: {bool(ffmpeg_path)}")
    click.echo(f"xdotool found: {bool(xdotool_path)}")
    click.echo(f"wtype found: {bool(wtype_path)}")


@doctor_group.command("systemd")
def doctor_systemd() -> None:
    """Check systemd user services and config propagation."""
    if not systemctl_path():
        click.echo("systemctl not found (is systemd installed?)", err=True)
        return

    env_path = env_file_path()
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

    click.echo(f"env file: {env_path} exists: {env_path.exists()}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(f"transcribe backend (env file): {backend}")
    click.echo(f"env file has OPENAI_API_KEY: {has_openai_key}")
    click.echo(f"env file has ELEVENLABS_API_KEY/XI_API_KEY: {has_eleven_key}")
    click.echo(f"OPENAI_API_KEY env set (this process): {bool(os.environ.get('OPENAI_API_KEY'))}")
    click.echo(
        "ELEVENLABS_API_KEY/XI_API_KEY env set (this process): "
        f"{bool((os.environ.get('ELEVENLABS_API_KEY') or '').strip() or (os.environ.get('XI_API_KEY') or '').strip())}"
    )

    # Basic unit status
    units = [TARGET_UNIT, RECORDER_UNIT, TRANSCRIBER_UNIT]
    props_wanted = [
        "LoadState",
        "ActiveState",
        "SubState",
        "UnitFileState",
        "FragmentPath",
    ]
    for unit in units:
        props = systemctl_show_properties(unit, props_wanted)
        load_state = props.get("LoadState", "")
        active_state = props.get("ActiveState", "")
        sub_state = props.get("SubState", "")
        unit_file_state = props.get("UnitFileState", "")
        fragment = props.get("FragmentPath", "")
        err = props.get("error", "")

        click.echo(f"unit: {unit}")
        if err and not load_state:
            click.echo(f"  error: {err}")
            continue
        click.echo(f"  LoadState: {load_state}")
        click.echo(f"  UnitFileState: {unit_file_state}")
        click.echo(f"  ActiveState: {active_state} ({sub_state})")
        if fragment:
            click.echo(f"  FragmentPath: {fragment}")

        cat = systemctl_cat(unit)
        if cat.returncode != 0:
            click.echo(f"  systemctl cat failed: {(cat.stderr or '').strip()}")
            continue

        unit_text = cat.stdout or ""
        if unit == TARGET_UNIT:
            wants_both = (RECORDER_UNIT in unit_text) and (TRANSCRIBER_UNIT in unit_text)
            click.echo(f"  unit wants recorder+transcriber: {wants_both}")
        else:
            has_env_file = "/.config/voicepipe/voicepipe.env" in unit_text
            click.echo(f"  unit references voicepipe.env: {has_env_file}")
            part_of_target = f"PartOf={TARGET_UNIT}" in unit_text
            click.echo(f"  unit PartOf {TARGET_UNIT}: {part_of_target}")

    # Suggested fixes
    if backend == "elevenlabs":
        if not has_eleven_key and not (
            (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
            or (os.environ.get("XI_API_KEY") or "").strip()
        ):
            click.echo("missing api key: set it with:", err=True)
            click.echo("  voicepipe setup --backend elevenlabs", err=True)
            click.echo("  voicepipe config set-elevenlabs-key --from-stdin", err=True)

        click.echo("quick setup (recommended):", err=True)
        click.echo("  voicepipe setup --backend elevenlabs", err=True)
    else:
        if not has_openai_key and not (
            os.environ.get("OPENAI_API_KEY") or ""
        ).strip():
            click.echo("missing api key: set it with:", err=True)
            click.echo("  voicepipe setup", err=True)
            click.echo("  voicepipe config set-openai-key --from-stdin", err=True)

        click.echo("quick setup (recommended):", err=True)
        click.echo("  voicepipe setup", err=True)

    click.echo("common fixes:", err=True)
    click.echo("  voicepipe service install", err=True)
    click.echo("  voicepipe service enable", err=True)
    click.echo("  voicepipe service start", err=True)
    click.echo("  voicepipe service restart", err=True)
    click.echo(f"  systemctl --user restart {TARGET_UNIT}", err=True)


def _doctor_daemon(
    *,
    record_test: bool,
    transcribe_test: bool,
    record_seconds: float,
    play: bool,
    cleanup: bool,
) -> None:
    """Check daemon socket/health and (optionally) perform record/transcribe tests."""
    socket_path = find_daemon_socket_path()
    runtime_path = runtime_app_dir()
    click.echo(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    click.echo(f"daemon socket: {socket_path or '(not found)'}")
    click.echo(f"daemon socket candidates: {', '.join(str(p) for p in daemon_socket_paths())}")

    # Daemon ping (avoid falling back to subprocess mode)
    if socket_path is not None and socket_path.exists():
        t0 = time.time()
        try:
            resp = send_request("status", socket_path=socket_path)
        except IpcError as e:
            resp = {"error": str(e)}
        dt_ms = int((time.time() - t0) * 1000)
        click.echo(f"daemon status ms: {dt_ms}")
        click.echo(f"daemon status resp: {resp}")
    else:
        click.echo("daemon status: skipped (daemon socket missing)", err=True)

    recorded_file: str | None = None
    if record_test:
        if socket_path is None or not socket_path.exists():
            click.echo("record-test: skipped (daemon socket missing)", err=True)
        else:
            try:
                status = try_send_request("status", socket_path=socket_path) or {}
                if status.get("status") == "recording":
                    click.echo(
                        "record-test: skipped (daemon already recording)", err=True
                    )
                else:
                    click.echo(
                        f"record-test: recording for {record_seconds:.1f}s... speak now",
                        err=True,
                    )
                    start_resp = try_send_request(
                        "start", socket_path=socket_path
                    ) or {}
                    if start_resp.get("error"):
                        click.echo(
                            f"record-test start error: {start_resp.get('error')}",
                            err=True,
                        )
                    else:
                        time.sleep(max(0.1, float(record_seconds)))
                        stop_resp = try_send_request(
                            "stop", socket_path=socket_path
                        ) or {}
                        recorded_file = stop_resp.get("audio_file")
                        if stop_resp.get("error"):
                            click.echo(
                                f"record-test stop error: {stop_resp.get('error')}",
                                err=True,
                            )
                        elif recorded_file and Path(recorded_file).exists():
                            size = Path(recorded_file).stat().st_size
                            click.echo(f"record-test file: {recorded_file}")
                            click.echo(f"record-test bytes: {size}")
                            if cleanup:
                                click.echo(
                                    "record-test output: will delete (--cleanup)", err=True
                                )
                            else:
                                preserved = _preserve_doctor_audio_file(
                                    Path(recorded_file)
                                )
                                if str(preserved) != str(recorded_file):
                                    click.echo(f"record-test preserved: {preserved}")
                                recorded_file = str(preserved)
                        else:
                            click.echo(
                                "record-test: no audio file produced", err=True
                            )
            except Exception as e:
                click.echo(f"record-test error: {e}", err=True)

    if play and recorded_file and Path(recorded_file).exists():
        ffplay_path = shutil.which("ffplay")
        if not ffplay_path:
            click.echo("play: skipped (ffplay not found)", err=True)
        else:
            try:
                play_timeout = max(5.0, float(record_seconds) + 5.0)
                click.echo(
                    f"play: starting ffplay (timeout {play_timeout:.1f}s)...",
                    err=True,
                )
                proc = subprocess.Popen(
                    [
                        ffplay_path,
                        "-autoexit",
                        "-nodisp",
                        "-loglevel",
                        "error",
                        recorded_file,
                    ],
                    **(
                        {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
                        if is_windows()
                        else {"start_new_session": True}
                    ),
                )
                try:
                    proc.wait(timeout=play_timeout)
                except subprocess.TimeoutExpired:
                    click.echo("play: ffplay timed out, terminating...", err=True)
                    if not is_windows():
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except Exception:
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                    else:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        if not is_windows():
                            try:
                                os.killpg(proc.pid, signal.SIGKILL)
                            except Exception:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                        else:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass
                except KeyboardInterrupt:
                    click.echo("play: interrupted, terminating ffplay...", err=True)
                    if not is_windows():
                        try:
                            os.killpg(proc.pid, signal.SIGTERM)
                        except Exception:
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                    else:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    raise
            except Exception as e:
                click.echo(f"play error: {e}", err=True)

    if transcribe_test:
        if not recorded_file:
            click.echo("transcribe-test: skipped (no record-test file)", err=True)
        else:
            try:
                from voicepipe.config import get_transcribe_model
                from voicepipe.transcription import transcribe_audio_file

                model = get_transcribe_model()
                text = transcribe_audio_file(
                    recorded_file,
                    model=model,
                    prefer_daemon=True,
                )
                click.echo("transcribe-test text:")
                click.echo(text)
            except Exception as e:
                click.echo(f"transcribe-test error: {e}", err=True)

    if cleanup and recorded_file and Path(recorded_file).exists():
        try:
            Path(recorded_file).unlink()
        except Exception as e:
            click.echo(f"cleanup error: {e}", err=True)


@doctor_group.command("daemon")
@click.option(
    "--record-test",
    is_flag=True,
    help="Start/stop a daemon recording and report file size",
)
@click.option(
    "--transcribe-test",
    is_flag=True,
    help="Transcribe the record-test audio file",
)
@click.option(
    "--record-seconds",
    default=1.0,
    type=float,
    show_default=True,
    help="Seconds to record for --record-test",
)
@click.option(
    "--play",
    is_flag=True,
    help="Play the record-test file via ffplay (if available)",
)
@click.option(
    "--cleanup",
    is_flag=True,
    help="Delete record-test output after running (default: preserve)",
)
def doctor_daemon(
    record_test: bool,
    transcribe_test: bool,
    record_seconds: float,
    play: bool,
    cleanup: bool,
) -> None:
    _doctor_daemon(
        record_test=bool(record_test),
        transcribe_test=bool(transcribe_test),
        record_seconds=float(record_seconds),
        play=bool(play),
        cleanup=bool(cleanup),
    )


def _doctor_audio(seconds: float) -> None:
    """Record briefly and report microphone levels."""
    try:
        import numpy as np
        import sounddevice as sd
        from voicepipe.audio import select_audio_input
        from voicepipe.config import get_audio_channels, get_audio_sample_rate
    except Exception as e:
        click.echo(f"audio-test error: {e}", err=True)
        return

    try:
        env_device = os.environ.get("VOICEPIPE_DEVICE")
        preferred_device = int(env_device) if (env_device or "").isdigit() else None
        selection = select_audio_input(
            preferred_device_index=preferred_device,
            preferred_samplerate=get_audio_sample_rate(),
            preferred_channels=get_audio_channels(),
            strict_device_index=bool(preferred_device is not None),
        )
        fs = int(selection.samplerate)
        frames = int(max(0.01, float(seconds)) * fs)
        data = sd.rec(
            frames,
            samplerate=fs,
            channels=int(selection.channels),
            dtype="int16",
            device=int(selection.device_index),
        )
        sd.wait()
        max_amp = int(np.max(np.abs(data))) if data.size else 0
        click.echo(
            f"audio-test device={selection.device_index} samplerate={fs} "
            f"channels={selection.channels} max_amp={max_amp}"
        )
    except Exception as e:
        click.echo(f"audio-test error: {e}", err=True)


@doctor_group.command("audio")
@click.option(
    "--seconds",
    default=0.5,
    type=float,
    show_default=True,
    help="Seconds to record for microphone level test",
)
def doctor_audio(seconds: float) -> None:
    _doctor_audio(float(seconds))


@click.command("doctor-legacy", hidden=True)
@click.option("--audio-test", is_flag=True, help="Record 0.5s and report levels")
@click.option(
    "--record-test",
    is_flag=True,
    help="Start/stop a 1s daemon recording and report file size",
)
@click.option(
    "--transcribe-test", is_flag=True, help="Transcribe the record-test audio file"
)
@click.option(
    "--record-seconds",
    default=1.0,
    type=float,
    show_default=True,
    help="Seconds to record for --record-test",
)
@click.option(
    "--play",
    is_flag=True,
    help="Play the record-test file via ffplay (if available)",
)
def doctor_legacy(
    audio_test: bool,
    record_test: bool,
    transcribe_test: bool,
    record_seconds: float,
    play: bool,
) -> None:
    """DEPRECATED: use `voicepipe doctor env|daemon|audio`."""
    click.echo("doctor: deprecated; use `voicepipe doctor env|daemon|audio`", err=True)
    if audio_test:
        _doctor_audio(0.5)
    if record_test or transcribe_test or play:
        _doctor_daemon(
            record_test=bool(record_test),
            transcribe_test=bool(transcribe_test),
            record_seconds=float(record_seconds),
            play=bool(play),
            cleanup=False,
        )
