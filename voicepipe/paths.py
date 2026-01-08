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

from voicepipe.platform import getenv_path, is_macos, is_windows


APP_NAME = "voicepipe"

_PRIVATE_DIR_MODE = 0o700
_SOCKET_ENV_VARS_DAEMON: tuple[str, ...] = ("VOICEPIPE_DAEMON_SOCKET", "VOICEPIPE_SOCKET_PATH")
_SOCKET_ENV_VARS_TRANSCRIBER: tuple[str, ...] = (
    "VOICEPIPE_TRANSCRIBER_SOCKET",
    "VOICEPIPE_TRANSCRIBER_SOCKET_PATH",
)


def _ensure_private_dir(path: Path) -> None:
    """Best-effort ensure a directory is user-private (0700)."""
    try:
        os.chmod(path, _PRIVATE_DIR_MODE)
    except Exception:
        pass


def runtime_dir() -> Path:
    """Return the best-available per-user runtime base directory."""
    if is_windows():
        local_appdata = getenv_path("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / APP_NAME / "run"
        return Path(tempfile.gettempdir()) / APP_NAME

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
    if is_windows():
        # `runtime_dir()` already includes an app-specific suffix on Windows.
        path = base
    elif base == tmp_dir:
        path = tmp_dir / f"{APP_NAME}-{os.getuid()}"
    else:
        path = base / APP_NAME
    if not is_windows() and base != tmp_dir:
        try:
            # If the runtime dir isn't writable/executable, fall back to /tmp.
            if not base.is_dir() or not os.access(base, os.W_OK | os.X_OK):
                base = tmp_dir
                path = tmp_dir / f"{APP_NAME}-{os.getuid()}"
        except Exception:
            base = tmp_dir
            path = tmp_dir / f"{APP_NAME}-{os.getuid()}"

    if create:
        try:
            existed = path.exists()
        except Exception:
            existed = True

        try:
            path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
            _ensure_private_dir(path)

            # Windows quirk: stale directories under %TEMP% can be created by an
            # elevated process and end up not writable by the normal desktop
            # token, causing hotkey tools to fail with EACCES when creating the
            # lock/session files. Detect this and fall back.
            if is_windows() and existed:
                try:
                    fd, probe = tempfile.mkstemp(prefix="voicepipe_probe_", dir=str(path))
                    os.close(fd)
                    os.unlink(probe)
                except Exception:
                    raise PermissionError("runtime dir not writable")
        except Exception:
            if is_windows():
                # If LOCALAPPDATA is missing/misconfigured, fall back to TEMP.
                fallback = tmp_dir / APP_NAME
                fallback.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
                _ensure_private_dir(fallback)
                path = fallback
            else:
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


def _env_socket_path(var_names: tuple[str, ...]) -> Path | None:
    for var in var_names:
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        try:
            return Path(raw).expanduser()
        except Exception:
            continue
    return None


def daemon_socket_paths(*, include_legacy: bool = True) -> list[Path]:
    """Candidate socket paths for the recorder daemon.

    Order is important:
    - Explicit env override first (if set)
    - Canonical per-app runtime dir (current default)
    - Legacy historical locations (best-effort)
    """
    paths: list[Path] = []
    env_override = _env_socket_path(_SOCKET_ENV_VARS_DAEMON)
    if env_override is not None:
        paths.append(env_override)

    paths.append(daemon_socket_path())

    if include_legacy:
        # Legacy: socket directly under runtime dir (no per-app subdir).
        paths.append(runtime_dir() / "voicepipe.sock")
        # Older /tmp layouts.
        tmp = Path(tempfile.gettempdir())
        paths.append(tmp / "voicepipe.sock")
        paths.append(tmp / APP_NAME / "voicepipe.sock")

    # Deduplicate while preserving order.
    seen: set[Path] = set()
    ordered: list[Path] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    return ordered


def transcriber_socket_paths(*, include_legacy: bool = True) -> list[Path]:
    """Candidate socket paths for the transcriber daemon."""
    paths: list[Path] = []
    env_override = _env_socket_path(_SOCKET_ENV_VARS_TRANSCRIBER)
    if env_override is not None:
        paths.append(env_override)

    paths.append(transcriber_socket_path())

    if include_legacy:
        paths.append(runtime_dir() / "voicepipe_transcriber.sock")
        tmp = Path(tempfile.gettempdir())
        paths.append(tmp / "voicepipe_transcriber.sock")
        # Historical shared /tmp/voicepipe directory.
        paths.append(tmp / APP_NAME / "voicepipe_transcriber.sock")

    seen: set[Path] = set()
    ordered: list[Path] = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    return ordered


def find_existing_socket(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            if path.exists():
                return path
        except Exception:
            continue
    return None


def find_daemon_socket_path() -> Path | None:
    return find_existing_socket(daemon_socket_paths())


def find_transcriber_socket_path() -> Path | None:
    return find_existing_socket(transcriber_socket_paths())


def audio_tmp_dir(*, create: bool = False) -> Path:
    return runtime_app_dir(create=create)


def session_state_dir(*, create: bool = False) -> Path:
    return runtime_app_dir(create=create)


def state_dir(*, create: bool = False) -> Path:
    """Return a persistent per-user state directory (XDG_STATE_HOME)."""
    if is_windows():
        local_appdata = getenv_path("LOCALAPPDATA")
        if local_appdata:
            base = Path(local_appdata) / APP_NAME / "state"
        else:
            # Fallback: best-effort under the home directory.
            try:
                base = Path.home() / "AppData" / "Local" / APP_NAME / "state"
            except Exception:
                base = Path(tempfile.gettempdir()) / APP_NAME / "state"
        if create:
            base.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
            _ensure_private_dir(base)
        return base

    if is_macos():
        try:
            base = Path.home() / "Library" / "Application Support" / APP_NAME / "state"
        except Exception:
            base = Path(tempfile.gettempdir()) / APP_NAME / "state"
        if create:
            base.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
            _ensure_private_dir(base)
        return base

    xdg_state_home = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg_state_home) if xdg_state_home else (Path.home() / ".local" / "state")
    path = base / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
        _ensure_private_dir(path)
    return path


def logs_dir(*, create: bool = False) -> Path:
    """Return a persistent per-user logs directory."""
    if is_windows():
        local_appdata = getenv_path("LOCALAPPDATA")
        if local_appdata:
            path = Path(local_appdata) / APP_NAME / "logs"
        else:
            try:
                path = Path.home() / "AppData" / "Local" / APP_NAME / "logs"
            except Exception:
                path = Path(tempfile.gettempdir()) / APP_NAME / "logs"
    elif is_macos():
        try:
            path = Path.home() / "Library" / "Logs" / APP_NAME
        except Exception:
            path = Path(tempfile.gettempdir()) / APP_NAME / "logs"
    else:
        path = state_dir(create=False) / "logs"

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
