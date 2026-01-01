from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate $HOME + XDG dirs so tests never touch real user files."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    # Windows-style per-user dirs (safe to set on Unix; ignored there).
    appdata = tmp_path / "appdata"
    localappdata = tmp_path / "localappdata"
    appdata.mkdir(parents=True, exist_ok=True)
    localappdata.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    temp = tmp_path / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setenv("TMP", str(temp))

    runtime = tmp_path / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    # Avoid interacting with real display/typing tools unless a test opts in.
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XAUTHORITY", raising=False)

    # Avoid leaking developer/user config into tests.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("XI_API_KEY", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("VOICEPIPE_DEVICE", raising=False)
    monkeypatch.delenv("VOICEPIPE_TRANSCRIBE_BACKEND", raising=False)
    monkeypatch.delenv("VOICEPIPE_BACKEND", raising=False)
    monkeypatch.delenv("VOICEPIPE_TRANSCRIBE_MODEL", raising=False)
    monkeypatch.delenv("VOICEPIPE_MODEL", raising=False)

    # Ensure we don't accidentally rely on per-shell config location.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return home


@pytest.fixture()
def fake_systemd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_home: Path
) -> Path:
    """Provide fake `systemctl`/`journalctl` in PATH and a log file for calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    log_path = tmp_path / "systemctl.log"
    unit_dir = isolated_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("VOICEPIPE_TEST_SYSTEMCTL_LOG", str(log_path))
    monkeypatch.setenv("VOICEPIPE_TEST_SYSTEMD_UNIT_DIR", str(unit_dir))

    systemctl_path = bin_dir / "systemctl"
    systemctl_path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

def _log(argv):
    log_path = os.environ.get("VOICEPIPE_TEST_SYSTEMCTL_LOG")
    if not log_path:
        return
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(argv) + "\\n")
    except Exception:
        pass

argv = sys.argv[1:]
_log(argv)

# Be permissive: systemctl is called with many flags. Strip the ones we don't need.
args = [a for a in argv if a not in ("--user", "--no-pager", "--full")]
if not args:
    sys.exit(0)

cmd = args[0]
rest = args[1:]

if cmd == "cat":
    unit = rest[0] if rest else ""
    unit_dir = os.environ.get("VOICEPIPE_TEST_SYSTEMD_UNIT_DIR")
    if unit_dir:
        path = Path(unit_dir) / unit
        if path.exists():
            sys.stdout.write(path.read_text(encoding="utf-8"))
            sys.exit(0)
    sys.stderr.write("Unit not found\\n")
    sys.exit(1)

if cmd == "show":
    # Minimal `systemctl show UNIT -p Key -p Key2` support.
    props = []
    i = 0
    while i < len(rest):
        if rest[i] == "-p" and i + 1 < len(rest):
            props.append(rest[i + 1])
            i += 2
        else:
            i += 1
    for p in props:
        if p == "LoadState":
            sys.stdout.write("LoadState=loaded\\n")
        elif p == "ActiveState":
            sys.stdout.write("ActiveState=inactive\\n")
        elif p == "SubState":
            sys.stdout.write("SubState=dead\\n")
        elif p == "UnitFileState":
            sys.stdout.write("UnitFileState=disabled\\n")
        elif p == "FragmentPath":
            sys.stdout.write("FragmentPath=\\n")
        else:
            sys.stdout.write(f"{p}=\\n")
    sys.exit(0)

if cmd == "status":
    sys.stdout.write("fake systemctl status\\n")
    sys.exit(0)

# For enable/start/stop/restart/disable/daemon-reload/etc.
rc = int(os.environ.get("VOICEPIPE_TEST_SYSTEMCTL_RC", "0") or "0")
sys.exit(rc)
""",
        encoding="utf-8",
    )
    os.chmod(systemctl_path, stat.S_IRWXU)

    journalctl_path = bin_dir / "journalctl"
    journalctl_path.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ.get("VOICEPIPE_TEST_JOURNALCTL_LOG")
if log_path:
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(sys.argv[1:]) + "\\n")
    except Exception:
        pass

sys.stdout.write("fake journalctl\\n")
sys.exit(0)
""",
        encoding="utf-8",
    )
    os.chmod(journalctl_path, stat.S_IRWXU)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return log_path
