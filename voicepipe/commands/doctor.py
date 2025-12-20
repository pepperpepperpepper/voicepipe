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
    detect_openai_api_key,
    env_file_path,
    env_file_permissions_ok,
    legacy_api_key_paths,
    read_env_file,
)
from voicepipe.ipc import IpcError, daemon_socket_path, send_request, try_send_request
from voicepipe.paths import doctor_artifacts_dir, preserved_audio_dir, runtime_app_dir
from voicepipe.systemd import (
    RECORDER_UNIT,
    TARGET_UNIT,
    TRANSCRIBER_UNIT,
    systemctl_cat,
    systemctl_path,
    systemctl_show_properties,
)
from voicepipe.transcriber import WhisperTranscriber


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
    socket_path = daemon_socket_path()
    runtime_path = runtime_app_dir()

    click.echo(f"python: {sys.executable}")
    click.echo(f"cwd: {os.getcwd()}")
    click.echo(f"XDG_RUNTIME_DIR: {os.environ.get('XDG_RUNTIME_DIR', '')}")
    click.echo(f"DISPLAY: {os.environ.get('DISPLAY', '')}")
    click.echo(f"WAYLAND_DISPLAY: {os.environ.get('WAYLAND_DISPLAY', '')}")

    click.echo(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    click.echo(f"daemon socket exists: {socket_path} {socket_path.exists()}")

    click.echo(
        f"doctor artifacts dir: {doctor_artifacts_dir()} exists: {doctor_artifacts_dir().exists()}"
    )
    click.echo(
        f"preserved audio dir: {preserved_audio_dir()} exists: {preserved_audio_dir().exists()}"
    )

    # API key presence (never print the key)
    key_env = os.environ.get("OPENAI_API_KEY")
    key_env_file = env_file_path()
    click.echo(f"OPENAI_API_KEY env set: {bool(key_env)}")
    click.echo(f"env file exists: {key_env_file} {key_env_file.exists()}")
    for path in legacy_api_key_paths():
        click.echo(f"legacy key file exists: {path} {path.exists()}")
    click.echo(f"api key resolvable: {detect_openai_api_key()}")

    ffmpeg_path = shutil.which("ffmpeg")
    xdotool_path = shutil.which("xdotool")
    click.echo(f"ffmpeg found: {bool(ffmpeg_path)}")
    click.echo(f"xdotool found: {bool(xdotool_path)}")


@doctor_group.command("systemd")
def doctor_systemd() -> None:
    """Check systemd user services and config propagation."""
    if not systemctl_path():
        click.echo("systemctl not found (is systemd installed?)", err=True)
        return

    env_path = env_file_path()
    env_values = read_env_file(env_path)

    click.echo(f"env file: {env_path} exists: {env_path.exists()}")
    click.echo(f"env file perms 0600: {env_file_permissions_ok(env_path)}")
    click.echo(
        f"env file has OPENAI_API_KEY: {bool((env_values.get('OPENAI_API_KEY') or '').strip())}"
    )
    click.echo(
        f"OPENAI_API_KEY env set (this process): {bool(os.environ.get('OPENAI_API_KEY'))}"
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
    if not (env_values.get("OPENAI_API_KEY") or "").strip() and not (
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
    socket_path = daemon_socket_path()
    runtime_path = runtime_app_dir()
    click.echo(f"runtime dir: {runtime_path} exists: {runtime_path.exists()}")
    click.echo(f"daemon socket exists: {socket_path} {socket_path.exists()}")

    # Daemon ping (avoid falling back to subprocess mode)
    if socket_path.exists():
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
        if not socket_path.exists():
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
                    start_new_session=True,
                )
                try:
                    proc.wait(timeout=play_timeout)
                except subprocess.TimeoutExpired:
                    click.echo("play: ffplay timed out, terminating...", err=True)
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except Exception:
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except Exception:
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
                    try:
                        os.killpg(proc.pid, signal.SIGTERM)
                    except Exception:
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
                transcriber = WhisperTranscriber(model="whisper-1")
                text = transcriber.transcribe(recorded_file)
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
    except Exception as e:
        click.echo(f"audio-test error: {e}", err=True)
        return

    try:
        fs = 16000
        frames = int(max(0.01, float(seconds)) * fs)
        data = sd.rec(frames, samplerate=fs, channels=1, dtype="int16")
        sd.wait()
        max_amp = int(np.max(np.abs(data))) if data.size else 0
        click.echo(f"audio-test max_amp: {max_amp}")
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
