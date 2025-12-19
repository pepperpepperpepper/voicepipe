"""Configuration and environment loading for Voicepipe.

Voicepipe is used both interactively (from a shell) and as a systemd user
service. systemd user services generally do not load shell init files like
`.bashrc`/`.zshrc`, so configuration must come from a source systemd can read.

The canonical Voicepipe config file is:
  ~/.config/voicepipe/voicepipe.env
"""

from __future__ import annotations

import os
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

