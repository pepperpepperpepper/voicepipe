"""Configuration and environment loading for Voicepipe.

Voicepipe is used both interactively (from a shell) and as a systemd user
service. systemd user services generally do not load shell init files like
`.bashrc`/`.zshrc`, so configuration must come from a source systemd can read.

The canonical Voicepipe config file is OS-dependent:
  - Linux: ~/.config/voicepipe/voicepipe.env
  - Windows: %APPDATA%\\voicepipe\\voicepipe.env
  - macOS: ~/Library/Application Support/voicepipe/voicepipe.env

You can override it everywhere with `VOICEPIPE_ENV_FILE`.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Literal, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

from voicepipe.platform import getenv_path, is_windows
from voicepipe.platform import is_macos


APP_NAME = "voicepipe"
DEFAULT_TRANSCRIBE_BACKEND = "openai"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
DEFAULT_ELEVENLABS_TRANSCRIBE_MODEL = "scribe_v1"

# Recording/audio defaults.
DEFAULT_AUDIO_SAMPLE_RATE = 16000
DEFAULT_AUDIO_CHANNELS = 1

# Backward-compatible name (historically OpenAI-only).
DEFAULT_TRANSCRIBE_MODEL = DEFAULT_OPENAI_TRANSCRIBE_MODEL

_ENV_LOADED = False

DEFAULT_ENV_FILE_TEMPLATE = """# Voicepipe environment config (used by systemd services and the CLI)
# OPENAI_API_KEY=sk-...
# ELEVENLABS_API_KEY=...
# or: XI_API_KEY=...
# VOICEPIPE_DEVICE=12  # or: pulse:alsa_input.pci-0000_00_1f.3-platform-...-input-6
# VOICEPIPE_PULSE_SOURCE=alsa_input.pci-0000_00_1f.3-platform-...-input-6
# VOICEPIPE_AUDIO_SAMPLE_RATE=16000
# VOICEPIPE_AUDIO_CHANNELS=1
# VOICEPIPE_TRANSCRIBE_BACKEND=openai
# VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe
# VOICEPIPE_TYPE_BACKEND=auto  # Linux: wtype|xdotool, macOS: osascript, Windows: sendinput, or: none
# VOICEPIPE_DAEMON_MODE=auto  # auto|never|always
"""

DaemonMode = Literal["auto", "never", "always"]


class VoicepipeConfigError(RuntimeError):
    pass


def config_home() -> Path:
    # Prefer a stable, systemd-friendly location that does not depend on
    # shell-initialized environment variables (systemd user services do not
    # load `.bashrc`/`.zshrc`).
    if is_windows():
        # Prefer Roaming AppData for user config on Windows.
        base = getenv_path("APPDATA") or getenv_path("LOCALAPPDATA")
        if base:
            return Path(base)
        try:
            return Path.home() / "AppData" / "Roaming"
        except Exception:
            return Path(tempfile.gettempdir())
    if is_macos():
        try:
            return Path.home() / "Library" / "Application Support"
        except Exception:
            return Path(tempfile.gettempdir())
    return Path.home() / ".config"


def config_dir(*, create: bool = False) -> Path:
    path = config_home() / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def env_file_path() -> Path:
    override = getenv_path("VOICEPIPE_ENV_FILE")
    if override:
        try:
            path = Path(override).expanduser()
        except Exception:
            path = Path(override)
        if not path.is_absolute():
            try:
                path = (Path.cwd() / path).resolve()
            except Exception:
                pass
        return path
    return config_dir() / f"{APP_NAME}.env"


def device_override_path() -> Path:
    """Optional per-user audio device override file.

    This is a simple integer file containing a PortAudio input device index.
    It is used as a fallback when VOICEPIPE_DEVICE is not set.
    """
    return config_dir() / "device"


def device_cache_path() -> Path:
    """Cached auto-detected audio input selection (Linux)."""
    return config_dir() / "device_cache.json"


def legacy_api_key_paths() -> list[Path]:
    return [
        config_dir() / "api_key",
        Path.home() / ".voicepipe_api_key",
    ]


def load_environment(*, load_cwd_dotenv: bool = True) -> None:
    """Load Voicepipe configuration into environment variables.

    Precedence:
    - Existing process env always wins.
    - Then `voicepipe.env` (see `VOICEPIPE_ENV_FILE` / OS default) if present.
    - Then a local `.env` (optional) for developer convenience.
    """

    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    # Load the canonical env file ourselves so we don't depend on python-dotenv
    # (and so UTF-8 BOM files created by PowerShell/Notepad still work).
    try:
        env_path = env_file_path()
        values = read_env_file(env_path)
        for key, value in values.items():
            if key in os.environ:
                continue
            os.environ[key] = value
    except Exception:
        pass

    if load_cwd_dotenv:
        if load_dotenv is not None:
            try:
                load_dotenv(override=False)
            except Exception:
                pass


def get_transcribe_model(
    *, default: str | None = None, load_env: bool = True
) -> str:
    if load_env:
        load_environment()
    raw = (
        os.environ.get("VOICEPIPE_TRANSCRIBE_MODEL") or os.environ.get("VOICEPIPE_MODEL") or ""
    ).strip()
    if raw:
        return raw

    if default is not None:
        return str(default)

    backend = get_transcribe_backend(load_env=False)
    if backend == "elevenlabs":
        return DEFAULT_ELEVENLABS_TRANSCRIBE_MODEL
    return DEFAULT_OPENAI_TRANSCRIBE_MODEL


def _as_positive_int(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except Exception:
            return None
        return parsed if parsed > 0 else None
    return None


def get_audio_sample_rate(*, default: int = DEFAULT_AUDIO_SAMPLE_RATE, load_env: bool = True) -> int:
    """Preferred recording sample rate (Hz).

    Voicepipe will still probe/fallback to a working rate at runtime if PortAudio
    rejects the preferred value.
    """
    if load_env:
        load_environment()
    for name in ("VOICEPIPE_AUDIO_SAMPLE_RATE", "VOICEPIPE_SAMPLE_RATE"):
        parsed = _as_positive_int(os.environ.get(name))
        if parsed is not None:
            return int(parsed)
    return int(default)


def get_audio_channels(*, default: int = DEFAULT_AUDIO_CHANNELS, load_env: bool = True) -> int:
    """Preferred recording channels (1=mono, 2=stereo)."""
    if load_env:
        load_environment()
    for name in ("VOICEPIPE_AUDIO_CHANNELS", "VOICEPIPE_CHANNELS"):
        parsed = _as_positive_int(os.environ.get(name))
        if parsed is not None:
            return int(parsed)
    return int(default)


def _normalize_transcribe_backend(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return DEFAULT_TRANSCRIBE_BACKEND
    aliases = {
        "xi": "elevenlabs",
        "eleven-labs": "elevenlabs",
        "eleven": "elevenlabs",
    }
    return aliases.get(raw, raw)


def get_transcribe_backend(
    *, default: str = DEFAULT_TRANSCRIBE_BACKEND, load_env: bool = True
) -> str:
    if load_env:
        load_environment()
    raw = (
        os.environ.get("VOICEPIPE_TRANSCRIBE_BACKEND")
        or os.environ.get("VOICEPIPE_BACKEND")
        or default
    )
    return _normalize_transcribe_backend(str(raw))


def get_openai_api_key(*, load_env: bool = True) -> str:
    if load_env:
        load_environment()

    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if api_key:
        return api_key

    # Optional: systemd credentials (recommended over env vars for some users).
    # If configured (e.g. via `LoadCredential=`), systemd exposes secrets as
    # files under $CREDENTIALS_DIRECTORY.
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        for name in ("openai_api_key", "OPENAI_API_KEY"):
            try:
                cred_path = Path(cred_dir) / name
                if cred_path.exists():
                    api_key = cred_path.read_text(encoding="utf-8").strip()
                    if api_key:
                        return api_key
            except Exception:
                continue

    for path in legacy_api_key_paths():
        try:
            if path.exists():
                api_key = path.read_text(encoding="utf-8").strip()
                if api_key:
                    return api_key
        except Exception:
            continue

    raise VoicepipeConfigError(
        "OpenAI API key not found.\n\n"
        "Recommended (works for systemd services and CLI):\n"
        f"  Save it in: {env_file_path()}\n"
        "  Example line: OPENAI_API_KEY=sk-...\n\n"
        "Alternatives:\n"
        "  - Set OPENAI_API_KEY in the current environment\n"
        "  - Legacy file locations: ~/.config/voicepipe/api_key or ~/.voicepipe_api_key\n"
    )


def detect_openai_api_key(*, load_env: bool = True) -> bool:
    """Return True if an API key is available (never returns the key)."""
    try:
        _ = get_openai_api_key(load_env=load_env)
        return True
    except Exception:
        return False


def legacy_elevenlabs_key_paths() -> list[Path]:
    return [
        config_dir() / "elevenlabs_api_key",
        Path.home() / ".elevenlabs_api_key",
    ]


def get_elevenlabs_api_key(*, load_env: bool = True) -> str:
    if load_env:
        load_environment()

    for env_name in ("ELEVENLABS_API_KEY", "XI_API_KEY"):
        api_key = (os.environ.get(env_name) or "").strip()
        if api_key:
            return api_key

    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        for name in (
            "elevenlabs_api_key",
            "ELEVENLABS_API_KEY",
            "xi_api_key",
            "XI_API_KEY",
        ):
            try:
                cred_path = Path(cred_dir) / name
                if cred_path.exists():
                    api_key = cred_path.read_text(encoding="utf-8").strip()
                    if api_key:
                        return api_key
            except Exception:
                continue

    for path in legacy_elevenlabs_key_paths():
        try:
            if path.exists():
                api_key = path.read_text(encoding="utf-8").strip()
                if api_key:
                    return api_key
        except Exception:
            continue

    raise VoicepipeConfigError(
        "ElevenLabs API key not found.\n\n"
        "Recommended (works for systemd services and CLI):\n"
        f"  Save it in: {env_file_path()}\n"
        "  Example line: ELEVENLABS_API_KEY=...\n\n"
        "Alternatives:\n"
        "  - Set ELEVENLABS_API_KEY or XI_API_KEY in the current environment\n"
        "  - Legacy file locations: ~/.config/voicepipe/elevenlabs_api_key or ~/.elevenlabs_api_key\n"
    )


def detect_elevenlabs_api_key(*, load_env: bool = True) -> bool:
    """Return True if an ElevenLabs API key is available (never returns the key)."""
    try:
        _ = get_elevenlabs_api_key(load_env=load_env)
        return True
    except Exception:
        return False


def env_file_hint(*, create_dir: bool = False) -> str:
    """Human-friendly hint for where to put config."""
    path = env_file_path()
    if create_dir:
        try:
            config_dir(create=True)
        except Exception:
            pass
    return str(path)


def ensure_env_file(
    *,
    path: Optional[Path] = None,
    create_dir: bool = True,
    file_mode: int = 0o600,
    dir_mode: int = 0o700,
) -> Path:
    """Ensure the canonical env file exists (without setting any values)."""

    env_path = env_file_path() if path is None else Path(path)
    if create_dir:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_path(env_path.parent, dir_mode)

    if not env_path.exists():
        _atomic_write(env_path, DEFAULT_ENV_FILE_TEMPLATE)

    ensure_private_path(env_path, file_mode)
    return env_path


def read_env_file(path: Optional[Path] = None) -> dict[str, str]:
    """Best-effort parse of a dotenv-style file (no interpolation).

    This is intentionally simple and only supports the subset Voicepipe needs.
    """

    env_path = env_file_path() if path is None else Path(path)
    if not env_path.exists():
        return {}

    out: dict[str, str] = {}
    try:
        # Use utf-8-sig so UTF-8 BOM files (common on Windows) are accepted.
        for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].lstrip()
            if "=" not in line:
                continue
            key, _sep, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            # Strip simple quoting.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            out[key] = value
    except Exception:
        return {}
    return out


def _atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp_path, path)
        return
    except Exception as e:
        # On Windows, `os.replace()` can fail when the destination file is open in an editor.
        try:
            path.write_text(content, encoding="utf-8")
            err = getattr(sys, "stderr", None)
            if err is not None:
                try:
                    print(
                        f"Warning: failed to atomically replace {path} ({e}); wrote in-place instead.",
                        file=err,
                    )
                except Exception:
                    pass
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


def ensure_private_path(path: Path, mode: int) -> None:
    """Best-effort chmod; ignore failures on unsupported filesystems."""

    try:
        os.chmod(path, mode)
    except Exception:
        pass


def upsert_env_var(
    name: str,
    value: str,
    *,
    path: Optional[Path] = None,
    create_dir: bool = True,
    file_mode: int = 0o600,
    dir_mode: int = 0o700,
) -> Path:
    """Set or update an env var in the canonical env file; returns the path."""

    if "\n" in value or "\r" in value:
        raise ValueError("invalid value: must be single-line")

    env_path = env_file_path() if path is None else Path(path)
    if create_dir:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_path(env_path.parent, dir_mode)

    lines: list[str] = []
    if env_path.exists():
        try:
            # Accept UTF-8 BOM files (common on Windows) and preserve newlines.
            lines = env_path.read_text(encoding="utf-8-sig").splitlines(True)
        except Exception:
            lines = []

    def _is_target_line(raw_line: str) -> bool:
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            return False
        candidate = stripped
        if candidate.startswith("export "):
            candidate = candidate[len("export ") :].lstrip()
        key, _sep, _rest = candidate.partition("=")
        return key.strip() == name

    rendered = f"{name}={value}\n"

    found = False
    new_lines: list[str] = []
    for line in lines:
        if _is_target_line(line):
            new_lines.append(rendered)
            found = True
        else:
            new_lines.append(line)

    if not found:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] += "\n"
        # Separate from content with a newline when appending to a non-empty file.
        if new_lines and new_lines[-1].strip():
            new_lines.append("\n")
        new_lines.append(rendered)

    _atomic_write(env_path, "".join(new_lines))

    # Enforce file permissions (best-effort).
    ensure_private_path(env_path, file_mode)

    # If the module already loaded env vars, update the live env too.
    os.environ.setdefault(name, value)

    return env_path


def env_file_permissions_ok(path: Optional[Path] = None) -> Optional[bool]:
    if is_windows():
        return None
    env_path = env_file_path() if path is None else Path(path)
    try:
        st = env_path.stat()
    except Exception:
        return None
    mode = stat.S_IMODE(st.st_mode)
    return mode == 0o600


def get_daemon_mode(*, default: DaemonMode = "auto", load_env: bool = True) -> DaemonMode:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_DAEMON_MODE") or "").strip().lower()
    mode = raw or str(default)
    if mode not in ("auto", "never", "always"):
        raise VoicepipeConfigError(
            "Invalid VOICEPIPE_DAEMON_MODE.\n\n"
            "Expected one of: auto, never, always\n"
            f"Got: {raw!r}"
        )
    return mode  # type: ignore[return-value]
