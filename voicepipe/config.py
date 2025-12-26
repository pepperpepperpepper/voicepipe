"""Configuration and environment loading for Voicepipe.

Voicepipe is used both interactively (from a shell) and as a systemd user
service. systemd user services generally do not load shell init files like
`.bashrc`/`.zshrc`, so configuration must come from a source systemd can read.

Voicepipe uses two config files under `~/.config/voicepipe/`:
- `voicepipe.env` for secrets / env vars (systemd-friendly)
- `config.toml` for non-secret settings (wake prefixes, etc.)
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

try:  # Python 3.11+
    import tomllib  # type: ignore[attr-defined]
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]
    try:
        import tomli as _tomli  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover
        _tomli = None  # type: ignore[assignment]


APP_NAME = "voicepipe"
DEFAULT_TRANSCRIBE_BACKEND = "openai"
DEFAULT_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-transcribe"
DEFAULT_ELEVENLABS_TRANSCRIBE_MODEL = "scribe_v1"

# Intent routing (wake-prefix scan) defaults.
DEFAULT_INTENT_ROUTING_ENABLED = True
DEFAULT_INTENT_WAKE_PREFIXES: tuple[str, ...] = ("zwingli",)

# Zwingli (LLM postprocessor) defaults.
DEFAULT_ZWINGLI_MODEL = "gpt-4o-mini"
DEFAULT_ZWINGLI_TEMPERATURE = 0.0
DEFAULT_ZWINGLI_USER_PROMPT = ""
DEFAULT_ZWINGLI_SYSTEM_PROMPT = (
    "You are Voicepipe's zwingli processor. The user will provide a spoken instruction.\n"
    "Return only the exact text that should be typed/inserted. Do not add commentary."
)

# Error reporting defaults.
DEFAULT_ERROR_REPORTING_ENABLED = True

# Backward-compatible name (historically OpenAI-only).
DEFAULT_TRANSCRIBE_MODEL = DEFAULT_OPENAI_TRANSCRIBE_MODEL

_ENV_LOADED = False
_SETTINGS_LOADED = False
_SETTINGS: dict[str, Any] = {}

DEFAULT_ENV_FILE_TEMPLATE = """# Voicepipe environment config (used by systemd services and the CLI)
# OPENAI_API_KEY=sk-...
# ELEVENLABS_API_KEY=...
# or: XI_API_KEY=...
# VOICEPIPE_DEVICE=12
# VOICEPIPE_TRANSCRIBE_BACKEND=openai
# VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe
# VOICEPIPE_INTENT_ROUTING=1
# VOICEPIPE_INTENT_WAKE_PREFIXES=zwingli
# VOICEPIPE_ZWINGLI_MODEL=gpt-4o-mini
# VOICEPIPE_ZWINGLI_TEMPERATURE=0.0
# VOICEPIPE_ZWINGLI_USER_PROMPT=
# VOICEPIPE_ZWINGLI_SYSTEM_PROMPT=Return only the text to type.
# VOICEPIPE_ERROR_REPORTING=1
# VOICEPIPE_COMMANDS_STRICT=0
"""

DEFAULT_SETTINGS_FILE_TEMPLATE = """# Voicepipe settings (non-secret).
#
# This file is loaded by both the CLI and systemd-launched services.
# Secrets (API keys) should go in: voicepipe.env

[intent]
# Enable/disable intent routing entirely (wake-prefix scanning).
routing_enabled = true

# Wake prefixes that trigger "command mode" (a.k.a. zwingli mode).
wake_prefixes = ["zwingli"]

[zwingli]
# Model used when a transcript begins with a wake prefix.
model = "gpt-4o-mini"

# Temperature for the LLM (0.0 is deterministic).
temperature = 0.0

# Optional user prompt (sent as an additional user message before the spoken prompt).
# user_prompt = ""

# System prompt for zwingli processing.
# system_prompt = "Return only the text to type."

[errors]
# When enabled, Voicepipe will attempt to type error messages into the target window
# (useful for hotkey workflows where stderr isn't visible).
reporting_enabled = true
"""


class VoicepipeConfigError(RuntimeError):
    pass


def config_home() -> Path:
    # Prefer a stable, systemd-friendly location that does not depend on
    # shell-initialized environment variables (systemd user services do not
    # load `.bashrc`/`.zshrc`).
    return Path.home() / ".config"


def config_dir(*, create: bool = False) -> Path:
    path = config_home() / APP_NAME
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def env_file_path() -> Path:
    return config_dir() / f"{APP_NAME}.env"


def settings_file_path() -> Path:
    """Path to the non-secret settings file (TOML)."""
    return config_dir() / "config.toml"


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


def _load_settings() -> dict[str, Any]:
    """Load settings from ~/.config/voicepipe/config.toml (best-effort)."""
    global _SETTINGS_LOADED, _SETTINGS
    if _SETTINGS_LOADED:
        return _SETTINGS
    _SETTINGS_LOADED = True

    settings_path = settings_file_path()
    if not settings_path.exists():
        _SETTINGS = {}
        return _SETTINGS

    parser = tomllib or _tomli
    if parser is None:  # pragma: no cover
        _SETTINGS = {}
        return _SETTINGS

    try:
        _SETTINGS = parser.loads(settings_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except Exception:
        _SETTINGS = {}
    return _SETTINGS


def ensure_settings_file(
    *,
    path: Optional[Path] = None,
    create_dir: bool = True,
    file_mode: int = 0o600,
    dir_mode: int = 0o700,
) -> Path:
    """Ensure the canonical settings TOML exists (without setting any values)."""

    settings_path = settings_file_path() if path is None else Path(path)
    if create_dir:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        ensure_private_path(settings_path.parent, dir_mode)

    if not settings_path.exists():
        _atomic_write(settings_path, DEFAULT_SETTINGS_FILE_TEMPLATE)

    ensure_private_path(settings_path, file_mode)
    return settings_path


def read_settings_file(path: Optional[Path] = None) -> dict[str, Any]:
    settings_path = settings_file_path() if path is None else Path(path)
    if not settings_path.exists():
        return {}
    parser = tomllib or _tomli
    if parser is None:  # pragma: no cover
        return {}
    try:
        return parser.loads(settings_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    except Exception:
        return {}


def _get_settings_value(settings: dict[str, Any], dotted_key: str) -> Any:
    cur: Any = settings
    for part in dotted_key.split("."):
        if not isinstance(cur, dict):
            return None
        if part not in cur:
            return None
        cur = cur.get(part)
    return cur


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
    return None


def _as_str_list(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            return []
        parts = [p.strip() for p in raw.split(",")]
        return [p for p in parts if p]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            s = str(item).strip()
            if s:
                out.append(s)
        return out
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except Exception:
            return None
    return None


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


def get_intent_routing_enabled(*, default: bool = DEFAULT_INTENT_ROUTING_ENABLED, load_env: bool = True) -> bool:
    """Return True if wake-prefix intent routing is enabled.

    This controls whether Voicepipe scans the transcript for configured wake
    prefixes (e.g. "zwingli") and rewrites output accordingly.

    Env var: VOICEPIPE_INTENT_ROUTING=1|0 (also supports true/false, yes/no).
    """
    if load_env:
        load_environment()
    raw = (os.environ.get("VOICEPIPE_INTENT_ROUTING") or "").strip()
    if not raw:
        settings = _load_settings() if load_env else {}
        from_file = _as_bool(_get_settings_value(settings, "intent.routing_enabled"))
        return bool(default) if from_file is None else bool(from_file)
    lowered = raw.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    return bool(default)


def get_intent_wake_prefixes(
    *,
    default: Iterable[str] | None = None,
    load_env: bool = True,
) -> list[str]:
    """Return the configured wake prefixes.

    Env var: VOICEPIPE_INTENT_WAKE_PREFIXES=zwingli
    - Unset: returns defaults.
    - Empty string: returns [].
    """
    if load_env:
        load_environment()

    raw = os.environ.get("VOICEPIPE_INTENT_WAKE_PREFIXES")
    if raw is None:
        settings = _load_settings() if load_env else {}
        from_file = _as_str_list(_get_settings_value(settings, "intent.wake_prefixes"))
        if from_file is not None:
            return from_file
        prefixes = DEFAULT_INTENT_WAKE_PREFIXES if default is None else default
        return [p for p in (str(x).strip() for x in prefixes) if p]

    parts = [p.strip() for p in str(raw).split(",")]
    return [p for p in parts if p]


def get_zwingli_model(*, default: str = DEFAULT_ZWINGLI_MODEL, load_env: bool = True) -> str:
    """Return the model used for zwingli prompt processing.

    Precedence:
    - VOICEPIPE_ZWINGLI_MODEL env var
    - ~/.config/voicepipe/config.toml [zwingli].model
    - DEFAULT_ZWINGLI_MODEL
    """
    if load_env:
        load_environment()

    raw = (os.environ.get("VOICEPIPE_ZWINGLI_MODEL") or "").strip()
    if raw:
        return raw

    settings = _load_settings() if load_env else {}
    from_file = _get_settings_value(settings, "zwingli.model")
    if from_file is not None:
        value = str(from_file).strip()
        if value:
            return value

    return str(default)


def get_zwingli_temperature(
    *, default: float = DEFAULT_ZWINGLI_TEMPERATURE, load_env: bool = True
) -> float:
    """Return the temperature used for zwingli prompt processing."""
    if load_env:
        load_environment()

    raw = (os.environ.get("VOICEPIPE_ZWINGLI_TEMPERATURE") or "").strip()
    if raw:
        try:
            return float(raw)
        except Exception:
            pass

    settings = _load_settings() if load_env else {}
    from_file = _as_float(_get_settings_value(settings, "zwingli.temperature"))
    if from_file is not None:
        return float(from_file)

    return float(default)


def get_zwingli_user_prompt(
    *, default: str = DEFAULT_ZWINGLI_USER_PROMPT, load_env: bool = True
) -> str:
    """Return the optional user prompt used for zwingli prompt processing."""
    if load_env:
        load_environment()

    raw = os.environ.get("VOICEPIPE_ZWINGLI_USER_PROMPT")
    if raw is not None:
        return str(raw)

    settings = _load_settings() if load_env else {}
    from_file = _get_settings_value(settings, "zwingli.user_prompt")
    if isinstance(from_file, str):
        return from_file

    return str(default)


def get_zwingli_system_prompt(
    *, default: str = DEFAULT_ZWINGLI_SYSTEM_PROMPT, load_env: bool = True
) -> str:
    """Return the system prompt used for zwingli prompt processing."""
    if load_env:
        load_environment()

    raw = os.environ.get("VOICEPIPE_ZWINGLI_SYSTEM_PROMPT")
    if raw is not None:
        value = str(raw)
        if value.strip():
            return value

    settings = _load_settings() if load_env else {}
    from_file = _get_settings_value(settings, "zwingli.system_prompt")
    if isinstance(from_file, str) and from_file.strip():
        return from_file

    return str(default)


def get_error_reporting_enabled(
    *, default: bool = DEFAULT_ERROR_REPORTING_ENABLED, load_env: bool = True
) -> bool:
    """Return True if error reporting (typing errors into the target) is enabled."""
    if load_env:
        load_environment()

    raw = (os.environ.get("VOICEPIPE_ERROR_REPORTING") or "").strip()
    if raw:
        lowered = raw.lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        return bool(default)

    settings = _load_settings() if load_env else {}
    from_file = _as_bool(_get_settings_value(settings, "errors.reporting_enabled"))
    if from_file is not None:
        return bool(from_file)

    return bool(default)


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
