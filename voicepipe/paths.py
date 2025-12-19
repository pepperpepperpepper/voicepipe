"""Filesystem paths for Voicepipe runtime artifacts.

Voicepipe primarily stores transient artifacts (Unix sockets, temp audio files,
PID/session markers, etc.) in a per-user runtime directory:

- Prefer `XDG_RUNTIME_DIR` when available (systemd user sessions).
- Fall back to `/run/user/$UID` when present.
- Finally, fall back to the system temp directory, but with a per-user suffix to
  avoid cross-user collisions.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


APP_NAME = "voicepipe"

_PRIVATE_DIR_MODE = 0o700


def _ensure_private_dir(path: Path) -> None:
    """Best-effort ensure a directory is user-private (0700)."""
    try:
        os.chmod(path, _PRIVATE_DIR_MODE)
    except Exception:
        pass


def runtime_dir() -> Path:
    """Return the best-available per-user runtime base directory."""
    xdg_runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime_dir:
        candidate = Path(xdg_runtime_dir)
        if candidate.exists():
            return candidate

    run_user_dir = Path("/run/user") / str(os.getuid())
    if run_user_dir.exists():
        return run_user_dir

    return Path(tempfile.gettempdir())


def runtime_app_dir(*, create: bool = False) -> Path:
    """Return the per-user directory for Voicepipe runtime artifacts."""
    base = runtime_dir()

    # If we fell back to a global temp dir, make it per-user to avoid collisions.
    tmp_dir = Path(tempfile.gettempdir())
    if base != tmp_dir:
        try:
            # If the runtime dir isn't writable/executable, fall back to /tmp.
            if not base.is_dir() or not os.access(base, os.W_OK | os.X_OK):
                base = tmp_dir
        except Exception:
            base = tmp_dir
    if base == tmp_dir:
        path = tmp_dir / f"{APP_NAME}-{os.getuid()}"
    else:
        path = base / APP_NAME

    if create:
        try:
            path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
            _ensure_private_dir(path)
        except Exception:
            # If XDG_RUNTIME_DIR is misconfigured/unwritable, fall back to /tmp.
            fallback = tmp_dir / f"{APP_NAME}-{os.getuid()}"
            fallback.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
            _ensure_private_dir(fallback)
            path = fallback

    return path


def daemon_socket_path(*, create_dir: bool = False) -> Path:
    return runtime_app_dir(create=create_dir) / "voicepipe.sock"


def transcriber_socket_path(*, create_dir: bool = False) -> Path:
    return runtime_app_dir(create=create_dir) / "voicepipe_transcriber.sock"


def audio_tmp_dir(*, create: bool = False) -> Path:
    return runtime_app_dir(create=create)


def session_state_dir(*, create: bool = False) -> Path:
    return runtime_app_dir(create=create)


def state_dir(*, create: bool = False) -> Path:
    """Return a persistent per-user state directory (XDG_STATE_HOME)."""
    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else (Path.home() / ".local" / "state")
    path = base / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        _ensure_private_dir(path)
    return path


def preserved_audio_dir(*, create: bool = False) -> Path:
    """Where to keep audio files when transcription fails."""
    path = state_dir(create=create) / "audio"
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        _ensure_private_dir(path)
    return path


def doctor_artifacts_dir(*, create: bool = False) -> Path:
    """Where to keep artifacts produced by `voicepipe doctor`."""
    path = state_dir(create=create) / "doctor"
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        _ensure_private_dir(path)
    return path
