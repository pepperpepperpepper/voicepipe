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

import json
import os
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

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
DEFAULT_ZWINGLI_BACKEND = "groq"
DEFAULT_ZWINGLI_MODEL = "moonshotai/kimi-k2-instruct"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"

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
# GROQ_API_KEY=...  # optional (for Groq/OpenAI-compatible chat endpoints)
# VOICEPIPE_DEVICE=12  # or: pulse:alsa_input.pci-0000_00_1f.3-platform-...-input-6
# VOICEPIPE_PULSE_SOURCE=alsa_input.pci-0000_00_1f.3-platform-...-input-6
# VOICEPIPE_AUDIO_SAMPLE_RATE=16000
# VOICEPIPE_AUDIO_CHANNELS=1
# VOICEPIPE_TRANSCRIBE_BACKEND=openai
# VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe
# VOICEPIPE_TRANSCRIBE_PROMPT=  # extra transcription context (optional; appended to the built-in prompt)
# VOICEPIPE_TRANSCRIBE_PROMPT_APPEND_TRIGGERS=0  # append trigger words to the STT prompt (opt-in)
# VOICEPIPE_TYPE_BACKEND=auto  # typing backend override (optional)
#   Linux: auto|wayland|x11|wtype|xdotool|none
#   macOS: auto|osascript|none
#   Windows: auto|sendinput|none
# VOICEPIPE_DAEMON_MODE=auto  # auto|never|always
#
# Transcript commands config (non-secret):
# VOICEPIPE_TRIGGERS_JSON=  # optional path override for triggers.json (git-friendly)
#
# Transcript triggers (prefix-based; checked after transcription):
# VOICEPIPE_TRANSCRIPT_TRIGGERS=zwingli=strip,zwingly=strip
#   Actions: strip|dispatch|zwingli|shell
#
# Shell trigger action (action=shell):
# VOICEPIPE_SHELL_ALLOW=1
# VOICEPIPE_SHELL_TIMEOUT_SECONDS=10
#
# Replay behavior:
# VOICEPIPE_REPLAY_DEFAULT=clipboard  # default actions for `voicepipe replay` (print|clipboard|type|json; comma-separated)
#
# Zwingli LLM preprocessing (action=zwingli):
# VOICEPIPE_ZWINGLI_BACKEND=groq  # openai|groq
# VOICEPIPE_ZWINGLI_MODEL=moonshotai/kimi-k2-instruct
# VOICEPIPE_ZWINGLI_TEMPERATURE=0.2
# VOICEPIPE_ZWINGLI_SYSTEM_PROMPT=You are a dictation preprocessor. Output only the final text to type.
# VOICEPIPE_ZWINGLI_USER_PROMPT=
# VOICEPIPE_ZWINGLI_BASE_URL=https://api.groq.com/openai/v1
# VOICEPIPE_ZWINGLI_API_KEY=  # optional override; otherwise uses GROQ_API_KEY or OPENAI_API_KEY depending on backend
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


def api_keys_path() -> Path:
    """Best-effort path to a user-managed API keys file/dir (Unix-y convention)."""
    try:
        return Path.home() / ".api-keys"
    except Exception:
        return Path(".api-keys")


def _read_key_from_api_keys(*, env_name: str, dir_candidates: list[str]) -> str:
    # 1) ~/.api-keys as dotenv-style file (KEY=VALUE).
    try:
        path = api_keys_path()
        if path.is_file():
            values = read_env_file(path)
            value = (values.get(env_name) or "").strip()
            if value:
                return value
    except Exception:
        pass

    # 2) ~/.api-keys as directory of key files.
    try:
        path = api_keys_path()
        if path.is_dir():
            for name in dir_candidates:
                candidate = path / name
                try:
                    if candidate.is_file():
                        value = candidate.read_text(encoding="utf-8").strip()
                        if value:
                            return value
                except Exception:
                    continue
    except Exception:
        pass

    return ""


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


def get_transcribe_prompt(*, default: str = "", load_env: bool = True) -> str:
    if load_env:
        load_environment()
    if "VOICEPIPE_TRANSCRIBE_PROMPT" not in os.environ:
        return str(default)
    return (os.environ.get("VOICEPIPE_TRANSCRIBE_PROMPT") or "").strip()


def get_transcribe_prompt_append_triggers(
    *, default: bool = False, load_env: bool = True
) -> bool:
    if load_env:
        load_environment()
    if "VOICEPIPE_TRANSCRIBE_PROMPT_APPEND_TRIGGERS" not in os.environ:
        return bool(default)
    return _as_bool(
        os.environ.get("VOICEPIPE_TRANSCRIBE_PROMPT_APPEND_TRIGGERS"),
        default=bool(default),
    )


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

    from_api_keys = _read_key_from_api_keys(
        env_name="OPENAI_API_KEY",
        dir_candidates=["OPENAI_API_KEY", "openai_api_key", "openai"],
    )
    if from_api_keys:
        return from_api_keys

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
        "  - Put OPENAI_API_KEY in ~/.api-keys (file or directory)\n"
        "  - Legacy file locations: ~/.config/voicepipe/api_key or ~/.voicepipe_api_key\n"
    )


def detect_openai_api_key(*, load_env: bool = True) -> bool:
    """Return True if an API key is available (never returns the key)."""
    try:
        _ = get_openai_api_key(load_env=load_env)
        return True
    except Exception:
        return False


def get_groq_api_key(*, load_env: bool = True) -> str:
    if load_env:
        load_environment()
    api_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if api_key:
        return api_key

    # Optional: systemd credentials (recommended over env vars for some users).
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir:
        for name in ("groq_api_key", "GROQ_API_KEY"):
            try:
                cred_path = Path(cred_dir) / name
                if cred_path.exists():
                    api_key = cred_path.read_text(encoding="utf-8").strip()
                    if api_key:
                        return api_key
            except Exception:
                continue

    from_api_keys = _read_key_from_api_keys(
        env_name="GROQ_API_KEY",
        dir_candidates=["GROQ_API_KEY", "groq_api_key", "groq"],
    )
    if from_api_keys:
        return from_api_keys

    raise VoicepipeConfigError(
        "Groq API key not found.\n\n"
        "Set GROQ_API_KEY in your environment (or voicepipe.env),\n"
        "or put GROQ_API_KEY in ~/.api-keys (file or directory)."
    )


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


_DEFAULT_TRIGGERS_JSON_TEMPLATE_FALLBACK = """{
  \"version\": 1,
  \"triggers\": {
    \"zwingli\": { \"action\": \"dispatch\" },
    \"zwingly\": { \"action\": \"dispatch\" }
  },
  \"dispatch\": { \"unknown_verb\": \"strip\" },
  \"verbs\": {
    \"strip\": { \"type\": \"builtin\" },
    \"rewrite\": { \"type\": \"llm\", \"profile\": \"rewrite\" },
    \"shell\": { \"type\": \"llm\", \"profile\": \"shell\" },
    \"bash\": { \"type\": \"llm\", \"profile\": \"shell\" },
    \"email\": { \"type\": \"llm\", \"profile\": \"email_draft\" },
    \"execute\": { \"type\": \"execute\", \"enabled\": false, \"timeout_seconds\": 10 }
  },
  \"llm_profiles\": {
    \"rewrite\": {
      \"temperature\": 0.2,
      \"system_prompt\": \"You rewrite text for dictation. Output only the final text to type.\"
    },
    \"shell\": {
      \"temperature\": 0.0,
      \"system_prompt\": \"You write safe shell scripts. Output only the script text.\\\\nRequirements:\\\\n- Start with: #!/usr/bin/env bash\\\\n- No markdown, no backticks.\\\\n- Must be read-only and non-destructive.\\\\n- Prefer: ls -la \\\\\\\"$HOME\\\\\\\"\",
      \"user_prompt_template\": \"Write a shell script for: {{text}}\"
    },
    \"email_draft\": {
      \"temperature\": 0.0,
      \"system_prompt\": \"Draft an email. Output ONLY 3 lines:\\\\nTo: <recipient>\\\\nSubject: <subject>\\\\nBody: <body>\\\\nNo markdown, no extra lines.\",
      \"user_prompt_template\": \"Draft an email from this phrase: {{text}}\"
    }
  }
}
"""


def _load_default_triggers_json_template() -> str:
    try:
        asset_path = Path(__file__).resolve().parent / "assets" / "triggers.default.json"
        if asset_path.exists():
            return asset_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return _DEFAULT_TRIGGERS_JSON_TEMPLATE_FALLBACK


def ensure_triggers_json(
    *,
    path: Optional[Path] = None,
    create_dir: bool = True,
    overwrite: bool = False,
    file_mode: int = 0o600,
    dir_mode: int = 0o700,
) -> Path:
    """Ensure the triggers.json config exists (without overwriting user changes)."""
    triggers_path = triggers_json_path() if path is None else Path(path)
    if create_dir:
        triggers_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_path(triggers_path.parent, dir_mode)

    if overwrite or not triggers_path.exists():
        _atomic_write(triggers_path, _load_default_triggers_json_template())

    ensure_private_path(triggers_path, file_mode)
    return triggers_path


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


def _as_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return bool(value)
    raw = str(value).strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "off"}:
        return False
    return bool(default)


def get_intent_routing_enabled(*, default: bool = False, load_env: bool = True) -> bool:
    if load_env:
        load_environment()
    if "VOICEPIPE_INTENT_ROUTING_ENABLED" not in os.environ:
        return bool(default)
    return _as_bool(os.environ.get("VOICEPIPE_INTENT_ROUTING_ENABLED"), default=bool(default))


def get_intent_wake_prefixes(
    *,
    default: tuple[str, ...] = ("command", "computer"),
    load_env: bool = True,
) -> list[str]:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_INTENT_WAKE_PREFIXES") or "").strip()
    if not raw:
        return list(default)
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]

def get_transcript_triggers(
    *,
    default: dict[str, str] | None = None,
    load_env: bool = True,
) -> dict[str, str]:
    """Return transcript trigger->action mapping.

    These are lightweight text prefixes checked against the transcription result.
    If a trigger matches, the corresponding action is invoked to produce the
    final output text.

    Env format (comma-separated pairs):
      VOICEPIPE_TRANSCRIPT_TRIGGERS=zwingli=strip,zwingly=zwingli

    If the variable is present but empty, triggers are disabled.
    """
    if load_env:
        load_environment()

    env_name = "VOICEPIPE_TRANSCRIPT_TRIGGERS"
    raw = (os.environ.get(env_name) or "").strip()

    if env_name in os.environ and not raw:
        return {}

    if not raw:
        try:
            from_file = _load_transcript_triggers_json()
        except Exception:
            # If a triggers config exists but is invalid, fail closed: disable
            # triggers instead of applying unexpected defaults.
            from_file = {}
        if from_file is not None:
            return from_file
        return dict(default or {"zwingli": "strip", "zwingly": "strip"})

    out: dict[str, str] = {}
    for entry in raw.split(","):
        item = (entry or "").strip()
        if not item:
            continue
        if "=" in item:
            trigger, _sep, action = item.partition("=")
        elif ":" in item:
            trigger, _sep, action = item.partition(":")
        else:
            trigger, action = item, "strip"

        trigger = (trigger or "").strip().lower()
        action = (action or "").strip().lower()
        if not trigger:
            continue
        if not action:
            action = "strip"
        out[trigger] = action

    return out


@dataclass(frozen=True)
class TranscriptDispatchConfig:
    unknown_verb: str = "strip"


@dataclass(frozen=True)
class TranscriptPluginConfig:
    module: str | None = None
    path: str | None = None
    callable: str | None = None


@dataclass(frozen=True)
class TranscriptVerbConfig:
    action: str
    enabled: bool = True
    type: str = "action"
    profile: str | None = None
    timeout_seconds: float | None = None
    plugin: TranscriptPluginConfig | None = None
    destination: str | None = None


@dataclass(frozen=True)
class TranscriptLLMProfileConfig:
    model: str | None = None
    temperature: float | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    user_prompt_template: str | None = None


@dataclass(frozen=True)
class TranscriptCommandsConfig:
    triggers: dict[str, str] = field(default_factory=dict)
    dispatch: TranscriptDispatchConfig = field(default_factory=TranscriptDispatchConfig)
    verbs: dict[str, TranscriptVerbConfig] = field(default_factory=dict)
    llm_profiles: dict[str, TranscriptLLMProfileConfig] = field(default_factory=dict)


_TRANSCRIPT_COMMANDS_JSON_CACHE: tuple[
    float,
    tuple[
        TranscriptDispatchConfig,
        dict[str, TranscriptVerbConfig],
        dict[str, TranscriptLLMProfileConfig],
    ],
] | None = None


def invalidate_transcript_commands_cache() -> None:
    """Clear cached triggers/verbs/profiles config (forces disk re-read on next load)."""
    global _TRANSCRIPT_COMMANDS_JSON_CACHE
    global _TRIGGERS_JSON_CACHE
    _TRANSCRIPT_COMMANDS_JSON_CACHE = None
    _TRIGGERS_JSON_CACHE = None


def _parse_transcript_dispatch_json_obj(obj: dict[str, Any]) -> TranscriptDispatchConfig:
    section = obj.get("dispatch")
    if section is None:
        return TranscriptDispatchConfig()
    if not isinstance(section, dict):
        raise VoicepipeConfigError("Invalid triggers.json: 'dispatch' must be an object")

    raw_unknown = section.get("unknown_verb", "strip")
    unknown: str | None = None
    if isinstance(raw_unknown, str):
        unknown = raw_unknown
    elif isinstance(raw_unknown, dict):
        raw_action = raw_unknown.get("action")
        if isinstance(raw_action, str):
            unknown = raw_action
    else:
        raise VoicepipeConfigError("Invalid triggers.json: 'dispatch.unknown_verb' must be a string")

    resolved = (unknown or "").strip().lower() or "strip"
    return TranscriptDispatchConfig(unknown_verb=resolved)


def _parse_transcript_verbs_json_obj(obj: dict[str, Any]) -> dict[str, TranscriptVerbConfig]:
    section = obj.get("verbs")
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise VoicepipeConfigError("Invalid triggers.json: 'verbs' must be an object mapping verb->config")

    out: dict[str, TranscriptVerbConfig] = {}
    for raw_verb, raw_value in section.items():
        if not isinstance(raw_verb, str):
            continue
        verb = raw_verb.strip().lower()
        if not verb:
            continue

        plugin: TranscriptPluginConfig | None = None
        destination: str | None = None
        if isinstance(raw_value, str):
            action = raw_value
            enabled = True
            verb_type = "action"
            profile = None
            timeout_seconds = None
        elif isinstance(raw_value, dict):
            raw_type = raw_value.get("type", "action")
            verb_type = raw_type.strip().lower() if isinstance(raw_type, str) else "action"
            if not verb_type:
                verb_type = "action"

            default_enabled = False if verb_type in ("shell", "execute", "plugin") else True
            enabled_value = raw_value.get("enabled", default_enabled)
            if not isinstance(enabled_value, bool):
                raise VoicepipeConfigError(
                    f"Invalid triggers.json: verb {raw_verb!r} has non-boolean 'enabled'"
                )
            enabled = bool(enabled_value)

            raw_destination = raw_value.get("destination")
            if isinstance(raw_destination, str):
                cleaned = raw_destination.strip().lower()
                if cleaned:
                    if cleaned not in {"print", "clipboard", "type"}:
                        raise VoicepipeConfigError(
                            "Invalid triggers.json: verb "
                            f"{raw_verb!r} has invalid destination {cleaned!r} "
                            "(expected: print, clipboard, type)"
                        )
                    destination = cleaned

            profile = None
            raw_profile = raw_value.get("profile")
            if isinstance(raw_profile, str):
                candidate = raw_profile.strip().lower()
                profile = candidate or None

            timeout_seconds = None
            raw_timeout = raw_value.get("timeout_seconds")
            if isinstance(raw_timeout, (int, float)) and not isinstance(raw_timeout, bool):
                timeout_seconds = float(raw_timeout)

            if verb_type == "plugin":
                raw_plugin = raw_value.get("plugin")
                if not isinstance(raw_plugin, dict):
                    raise VoicepipeConfigError(
                        f"Invalid triggers.json: verb {raw_verb!r} 'plugin' must be an object"
                    )

                raw_callable = raw_plugin.get("callable")
                callable_name = raw_callable.strip() if isinstance(raw_callable, str) else ""

                raw_module = raw_plugin.get("module")
                module_name = raw_module.strip() if isinstance(raw_module, str) else ""

                raw_path = raw_plugin.get("path")
                plugin_path = raw_path.strip() if isinstance(raw_path, str) else ""

                if not callable_name:
                    raise VoicepipeConfigError(
                        f"Invalid triggers.json: verb {raw_verb!r} plugin missing 'callable'"
                    )
                if bool(module_name) == bool(plugin_path):
                    raise VoicepipeConfigError(
                        f"Invalid triggers.json: verb {raw_verb!r} plugin must set exactly one of 'module' or 'path'"
                    )
                plugin = TranscriptPluginConfig(
                    module=module_name or None,
                    path=plugin_path or None,
                    callable=callable_name,
                )

            raw_action = raw_value.get("action")
            action: str
            if verb_type == "plugin":
                action = "plugin"
            elif isinstance(raw_action, str) and raw_action.strip():
                action = raw_action
            elif verb_type == "builtin":
                action = verb
            elif verb_type == "llm":
                action = "zwingli"
            elif verb_type in ("shell", "execute"):
                action = "shell"
            else:
                action = verb
        else:
            raise VoicepipeConfigError(
                f"Invalid triggers.json: verb {raw_verb!r} must map to a string or an object"
            )

        resolved_action = (action or "").strip().lower() or "strip"
        out[verb] = TranscriptVerbConfig(
            action=resolved_action,
            enabled=bool(enabled),
            type=str(verb_type),
            profile=profile,
            timeout_seconds=timeout_seconds,
            plugin=plugin,
            destination=destination,
        )

    return out


def _parse_transcript_llm_profiles_json_obj(
    obj: dict[str, Any],
) -> dict[str, TranscriptLLMProfileConfig]:
    section = obj.get("llm_profiles")
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise VoicepipeConfigError(
            "Invalid triggers.json: 'llm_profiles' must be an object mapping profile->config"
        )

    out: dict[str, TranscriptLLMProfileConfig] = {}
    for raw_name, raw_value in section.items():
        if not isinstance(raw_name, str):
            continue
        name = raw_name.strip().lower()
        if not name:
            continue
        if not isinstance(raw_value, dict):
            raise VoicepipeConfigError(
                f"Invalid triggers.json: llm profile {raw_name!r} must be an object"
            )

        model = raw_value.get("model") if isinstance(raw_value.get("model"), str) else None
        model = (model or "").strip() or None

        temperature = None
        raw_temperature = raw_value.get("temperature")
        if isinstance(raw_temperature, (int, float)) and not isinstance(raw_temperature, bool):
            temperature = float(raw_temperature)

        system_prompt = (
            raw_value.get("system_prompt") if isinstance(raw_value.get("system_prompt"), str) else None
        )
        system_prompt = (system_prompt or "").strip() or None

        user_prompt = raw_value.get("user_prompt") if isinstance(raw_value.get("user_prompt"), str) else None
        user_prompt = (user_prompt or "").strip() or None

        user_prompt_template = (
            raw_value.get("user_prompt_template")
            if isinstance(raw_value.get("user_prompt_template"), str)
            else None
        )
        user_prompt_template = (user_prompt_template or "").strip() or None

        out[name] = TranscriptLLMProfileConfig(
            model=model,
            temperature=temperature,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_prompt_template=user_prompt_template,
        )

    return out


def _load_transcript_commands_json(
    *,
    path: Path | None = None,
) -> tuple[
    TranscriptDispatchConfig, dict[str, TranscriptVerbConfig], dict[str, TranscriptLLMProfileConfig]
] | None:
    config_path = triggers_json_path() if path is None else Path(path)
    try:
        st = config_path.stat()
    except FileNotFoundError:
        return None

    global _TRANSCRIPT_COMMANDS_JSON_CACHE
    try:
        mtime = float(st.st_mtime)
    except Exception:
        mtime = 0.0

    if _TRANSCRIPT_COMMANDS_JSON_CACHE is not None and _TRANSCRIPT_COMMANDS_JSON_CACHE[0] == mtime:
        cached_dispatch, cached_verbs, cached_profiles = _TRANSCRIPT_COMMANDS_JSON_CACHE[1]
        return cached_dispatch, dict(cached_verbs), dict(cached_profiles)

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except Exception as e:
        raise VoicepipeConfigError(f"Failed to read triggers config: {config_path} ({e})") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise VoicepipeConfigError(f"Invalid JSON in triggers config: {config_path} ({e})") from e

    if not isinstance(payload, dict):
        raise VoicepipeConfigError(
            f"Invalid triggers config: {config_path} must contain a JSON object"
        )

    # Reuse the triggers parser for version validation (and legacy key support).
    _parse_transcript_triggers_json_obj(payload)

    dispatch = _parse_transcript_dispatch_json_obj(payload)
    verbs = _parse_transcript_verbs_json_obj(payload)
    profiles = _parse_transcript_llm_profiles_json_obj(payload)
    _TRANSCRIPT_COMMANDS_JSON_CACHE = (mtime, (dispatch, dict(verbs), dict(profiles)))
    return dispatch, dict(verbs), dict(profiles)


def get_transcript_commands_config(
    *,
    default_triggers: dict[str, str] | None = None,
    load_env: bool = True,
) -> TranscriptCommandsConfig:
    triggers = get_transcript_triggers(default=default_triggers, load_env=load_env)

    # Load dispatch/verbs from triggers.json (best-effort). If the file is invalid,
    # triggers will already be disabled by `get_transcript_triggers()` (fail closed).
    dispatch = TranscriptDispatchConfig()
    verbs: dict[str, TranscriptVerbConfig] = {}
    llm_profiles: dict[str, TranscriptLLMProfileConfig] = {}
    try:
        loaded = _load_transcript_commands_json()
    except Exception:
        loaded = None
    if loaded is not None:
        dispatch, verbs, llm_profiles = loaded

    return TranscriptCommandsConfig(
        triggers=dict(triggers),
        dispatch=dispatch,
        verbs=dict(verbs),
        llm_profiles=dict(llm_profiles),
    )


_TRIGGERS_JSON_CACHE: tuple[float, dict[str, str] | None] | None = None


def triggers_json_path() -> Path:
    """Canonical triggers config file path (non-secret)."""
    override = getenv_path("VOICEPIPE_TRIGGERS_JSON")
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
    return config_dir() / "triggers.json"


def _parse_transcript_triggers_json_obj(obj: dict[str, Any]) -> dict[str, str] | None:
    raw_version = obj.get("version", 1)
    if not isinstance(raw_version, int):
        raise VoicepipeConfigError("Invalid triggers.json: 'version' must be an integer")
    if raw_version != 1:
        raise VoicepipeConfigError(f"Unsupported triggers.json version: {raw_version}")

    if "triggers" in obj:
        section = obj.get("triggers")
    elif "transcript_triggers" in obj:
        section = obj.get("transcript_triggers")
    else:
        return None

    if not isinstance(section, dict):
        raise VoicepipeConfigError(
            "Invalid triggers.json: expected 'triggers' to be an object mapping trigger->action"
        )

    out: dict[str, str] = {}
    for raw_trigger, raw_value in section.items():
        if not isinstance(raw_trigger, str):
            continue
        trigger = raw_trigger.strip().lower()
        if not trigger:
            continue

        action: str | None = None
        if isinstance(raw_value, str):
            action = raw_value
        elif isinstance(raw_value, dict):
            raw_action = raw_value.get("action")
            if isinstance(raw_action, str):
                action = raw_action
        else:
            raise VoicepipeConfigError(
                f"Invalid triggers.json: trigger {raw_trigger!r} must map to a string or an object"
            )

        action = (action or "").strip().lower() or "strip"
        out[trigger] = action

    return out


def _load_transcript_triggers_json(*, path: Path | None = None) -> dict[str, str] | None:
    """Load transcript triggers from triggers.json.

    Returns:
      - dict (possibly empty) when the file exists and contains a triggers mapping
      - None when the file is missing or does not define triggers

    Raises:
      - VoicepipeConfigError when the file exists but is invalid.
    """

    config_path = triggers_json_path() if path is None else Path(path)
    try:
        st = config_path.stat()
    except FileNotFoundError:
        return None

    global _TRIGGERS_JSON_CACHE
    try:
        mtime = float(st.st_mtime)
    except Exception:
        mtime = 0.0

    if _TRIGGERS_JSON_CACHE is not None and _TRIGGERS_JSON_CACHE[0] == mtime:
        cached = _TRIGGERS_JSON_CACHE[1]
        return dict(cached) if cached is not None else None

    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except Exception as e:
        raise VoicepipeConfigError(f"Failed to read triggers config: {config_path} ({e})") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise VoicepipeConfigError(f"Invalid JSON in triggers config: {config_path} ({e})") from e

    if not isinstance(payload, dict):
        raise VoicepipeConfigError(
            f"Invalid triggers config: {config_path} must contain a JSON object"
        )

    triggers = _parse_transcript_triggers_json_obj(payload)
    _TRIGGERS_JSON_CACHE = (mtime, dict(triggers) if triggers is not None else None)
    return dict(triggers) if triggers is not None else None


def get_zwingli_backend(*, default: str = DEFAULT_ZWINGLI_BACKEND, load_env: bool = True) -> str:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_BACKEND") or "").strip().lower()
    return raw or str(default)


def get_zwingli_model(*, default: str = DEFAULT_ZWINGLI_MODEL, load_env: bool = True) -> str:
    if load_env:
        load_environment()
    return (os.environ.get("VOICEPIPE_ZWINGLI_MODEL") or str(default) or "").strip()


def get_zwingli_temperature(*, default: float = 0.2, load_env: bool = True) -> float:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_TEMPERATURE") or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def get_zwingli_system_prompt(
    *,
    default: str = "You are a dictation preprocessor. Output only the final text to type.",
    load_env: bool = True,
) -> str:
    if load_env:
        load_environment()
    return (os.environ.get("VOICEPIPE_ZWINGLI_SYSTEM_PROMPT") or str(default) or "").strip()


def get_zwingli_user_prompt(*, default: str = "", load_env: bool = True) -> str:
    if load_env:
        load_environment()
    return (os.environ.get("VOICEPIPE_ZWINGLI_USER_PROMPT") or str(default) or "").strip()


def get_zwingli_base_url(*, default: str = "", load_env: bool = True) -> str:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_BASE_URL") or "").strip()
    if raw:
        return raw

    backend = get_zwingli_backend(load_env=False)
    if backend == "groq":
        return DEFAULT_GROQ_BASE_URL

    return str(default).strip()


def get_zwingli_api_key(*, default: str = "", load_env: bool = True) -> str:
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_API_KEY") or "").strip()
    if raw:
        return raw

    backend = get_zwingli_backend(load_env=False)
    if backend == "groq":
        raw_groq = (os.environ.get("GROQ_API_KEY") or "").strip()
        if raw_groq:
            return raw_groq
        from_api_keys = _read_key_from_api_keys(
            env_name="GROQ_API_KEY",
            dir_candidates=["GROQ_API_KEY", "groq_api_key", "groq"],
        )
        if from_api_keys:
            return from_api_keys

    raw_openai = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if raw_openai:
        return raw_openai
    from_api_keys = _read_key_from_api_keys(
        env_name="OPENAI_API_KEY",
        dir_candidates=["OPENAI_API_KEY", "openai_api_key", "openai"],
    )
    if from_api_keys:
        return from_api_keys

    return str(default).strip()
