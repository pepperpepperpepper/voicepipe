"""systemd helpers for managing Voicepipe user services."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from voicepipe.config import env_file_path


RECORDER_UNIT = "voicepipe-recorder.service"
TRANSCRIBER_UNIT = "voicepipe-transcriber.service"
TARGET_UNIT = "voicepipe.target"


def user_unit_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def systemctl_path() -> Optional[str]:
    return shutil.which("systemctl")


def journalctl_path() -> Optional[str]:
    return shutil.which("journalctl")


def selected_units(*, recorder: bool = False, transcriber: bool = False) -> list[str]:
    if recorder or transcriber:
        units: list[str] = []
        if recorder:
            units.append(RECORDER_UNIT)
        if transcriber:
            units.append(TRANSCRIBER_UNIT)
        return units
    return [RECORDER_UNIT, TRANSCRIBER_UNIT]


def _execstart_python_module(module: str, args: Optional[list[str]] = None) -> str:
    argv = [sys.executable, "-m", module]
    if args:
        argv.extend(args)
    # systemd uses its own parsing rules; we avoid shell quoting by emitting a
    # space-joined string and assuming sys.executable has no spaces.
    return " ".join(argv)


def render_recorder_unit() -> str:
    return f"""[Unit]
Description=Voicepipe Recording Service
After=graphical-session.target
Wants=graphical-session.target
PartOf={TARGET_UNIT}

[Service]
Type=simple
ExecStart={_execstart_python_module("voicepipe.cli", ["daemon"])}
Restart=on-failure
RestartSec=5
Environment="PATH=/usr/local/bin:/usr/bin:/bin:%h/.local/bin"
Environment="HOME=%h"
EnvironmentFile=-%h/.config/voicepipe/voicepipe.env
# Import display environment dynamically
PassEnvironment=DISPLAY XAUTHORITY WAYLAND_DISPLAY

# Security hardening
PrivateTmp=false
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/tmp %t
NoNewPrivileges=true

[Install]
WantedBy=default.target
"""


def render_transcriber_unit() -> str:
    return f"""[Unit]
Description=Voicepipe Transcriber Service
After=network.target
PartOf={TARGET_UNIT}

[Service]
Type=simple
ExecStartPre=mkdir -p %t/voicepipe
ExecStart={_execstart_python_module("voicepipe.transcriber_daemon")}
Restart=always
RestartSec=1
PrivateTmp=false
Environment="PYTHONUNBUFFERED=1"
Environment="HOME=%h"
EnvironmentFile=-%h/.config/voicepipe/voicepipe.env

[Install]
WantedBy=default.target
"""


def render_target_unit() -> str:
    return f"""[Unit]
Description=Voicepipe (Recorder + Transcriber)
Wants={RECORDER_UNIT} {TRANSCRIBER_UNIT}

[Install]
WantedBy=default.target
"""


@dataclass(frozen=True)
class UnitInstallResult:
    recorder_path: Path
    transcriber_path: Path
    target_path: Path


def install_user_units(*, unit_dir: Optional[Path] = None) -> UnitInstallResult:
    dest_dir = user_unit_dir() if unit_dir is None else Path(unit_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    recorder_path = dest_dir / RECORDER_UNIT
    transcriber_path = dest_dir / TRANSCRIBER_UNIT
    target_path = dest_dir / TARGET_UNIT

    recorder_path.write_text(render_recorder_unit(), encoding="utf-8")
    transcriber_path.write_text(render_transcriber_unit(), encoding="utf-8")
    target_path.write_text(render_target_unit(), encoding="utf-8")

    return UnitInstallResult(
        recorder_path=recorder_path,
        transcriber_path=transcriber_path,
        target_path=target_path,
    )


def run_systemctl(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    systemctl = systemctl_path()
    if not systemctl:
        raise RuntimeError("systemctl not found (is systemd installed?)")
    cmd = [systemctl, "--user", *args]
    return subprocess.run(cmd, check=check)


def systemctl_show_properties(
    unit: str, properties: list[str]
) -> dict[str, str]:
    systemctl = systemctl_path()
    if not systemctl:
        raise RuntimeError("systemctl not found (is systemd installed?)")
    cmd = [systemctl, "--user", "show", unit, *sum([["-p", p] for p in properties], [])]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if "=" in line:
            k, _sep, v = line.partition("=")
            out[k] = v
    if proc.returncode != 0:
        out.setdefault("error", (proc.stderr or "").strip())
    return out


def systemctl_cat(unit: str) -> subprocess.CompletedProcess:
    systemctl = systemctl_path()
    if not systemctl:
        raise RuntimeError("systemctl not found (is systemd installed?)")
    cmd = [systemctl, "--user", "cat", unit]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def env_file_exists() -> bool:
    try:
        return env_file_path().exists()
    except Exception:
        return False


def systemd_credentials_supported() -> bool:
    # If the env var exists, systemd is exposing credentials to the process.
    return bool(os.environ.get("CREDENTIALS_DIRECTORY"))
