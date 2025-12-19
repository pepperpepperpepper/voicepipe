"""Configuration and environment loading for Voicepipe.

Voicepipe is used both interactively (from a shell) and as a systemd user
service. systemd user services generally do not load shell init files like
`.bashrc`/`.zshrc`, so configuration must come from a source systemd can read.

The canonical Voicepipe config file is:
  ~/.config/voicepipe/voicepipe.env
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


APP_NAME = "voicepipe"
DEFAULT_TRANSCRIBE_MODEL = "gpt-4o-transcribe"

_ENV_LOADED = False


class VoicepipeConfigError(RuntimeError):
    pass


def config_home() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home)
    return Path.home() / ".config"


def config_dir(*, create: bool = False) -> Path:
    path = config_home() / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def env_file_path() -> Path:
    return config_dir() / f"{APP_NAME}.env"


def legacy_api_key_paths() -> list[Path]:
    return [
        config_dir() / "api_key",
        Path.home() / ".voicepipe_api_key",
    ]


def load_environment(*, load_cwd_dotenv: bool = True) -> None:
    """Load Voicepipe configuration into environment variables.

    Precedence:
    - Existing process env always wins.
    - Then `~/.config/voicepipe/voicepipe.env` (if present).
    - Then a local `.env` (optional) for developer convenience.
    """

    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    if load_dotenv is None:
        return

    try:
        env_path = env_file_path()
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
    except Exception:
        pass

    if load_cwd_dotenv:
        try:
            load_dotenv(override=False)
        except Exception:
            pass


def get_transcribe_model(
    *, default: str = DEFAULT_TRANSCRIBE_MODEL, load_env: bool = True
) -> str:
    if load_env:
        load_environment()
    return (
        os.environ.get("VOICEPIPE_TRANSCRIBE_MODEL")
        or os.environ.get("VOICEPIPE_MODEL")
        or default
    )


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


def env_file_hint(*, create_dir: bool = False) -> str:
    """Human-friendly hint for where to put config."""
    path = env_file_path()
    if create_dir:
        try:
            config_dir(create=True)
        except Exception:
            pass
    return str(path)


def read_env_file(path: Optional[Path] = None) -> dict[str, str]:
    """Best-effort parse of a dotenv-style file (no interpolation).

    This is intentionally simple and only supports the subset Voicepipe needs.
    """

    env_path = env_file_path() if path is None else Path(path)
    if not env_path.exists():
        return {}

    out: dict[str, str] = {}
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
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
    os.replace(tmp_path, path)


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
            lines = env_path.read_text(encoding="utf-8").splitlines(True)
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
    env_path = env_file_path() if path is None else Path(path)
    try:
        st = env_path.stat()
    except Exception:
        return None
    mode = stat.S_IMODE(st.st_mode)
    return mode == 0o600
