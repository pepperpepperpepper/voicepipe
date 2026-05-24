"""Best-effort JSON-line debug log for Zwingli dispatch events.

A separate module so the file-rotation / payload-truncation noise doesn't
clutter the action-handler module. Everything here swallows its own
exceptions — logging must never break the text-output path.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


_ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES = 20 * 1024 * 1024


def _zwingli_debug_log_max_bytes() -> int:
    """Resolve the debug-log rotation threshold from the environment.

    Accepts raw bytes ("1048576") or a K/M/G suffix ("20M", "1.5G"). A value
    of 0 disables rotation (file grows without bound). Empty, malformed, or
    negative values fall back to the default.
    """
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES") or "").strip()
    if not raw:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES

    multiplier = 1
    suffix = raw[-1:].lower()
    if suffix in ("k", "m", "g"):
        multiplier = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[suffix]
        raw = raw[:-1].strip()

    try:
        value = float(raw)
    except ValueError:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES
    if value < 0:
        return _ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES
    return int(value * multiplier)


def _zwingli_debug_log_enabled() -> bool:
    raw = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG") or "").strip().lower()
    if raw in {"0", "false", "f", "no", "n", "off"}:
        return False
    return True


def _zwingli_debug_log_path() -> Path:
    override = (os.environ.get("VOICEPIPE_ZWINGLI_DEBUG_LOG_FILE") or "").strip()
    if override:
        try:
            return Path(override).expanduser()
        except Exception:
            return Path(override)
    if os.name != "nt":
        return Path("/tmp/zwingli-debug.log")
    try:
        return Path(tempfile.gettempdir()) / "zwingli-debug.log"
    except Exception:
        return Path("zwingli-debug.log")


def _truncate_for_log(value: object, *, max_chars: int = 20_000) -> object:
    if not isinstance(value, str):
        return value
    if max_chars <= 0:
        return ""
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _maybe_rotate_debug_log(path: Path) -> None:
    max_bytes = _zwingli_debug_log_max_bytes()
    if max_bytes <= 0:
        return

    try:
        st = path.stat()
    except FileNotFoundError:
        return
    except Exception:
        return

    try:
        size = int(getattr(st, "st_size", 0) or 0)
    except Exception:
        size = 0
    if size <= max_bytes:
        return

    backup = Path(str(path) + ".1")
    try:
        try:
            backup.unlink(missing_ok=True)
        except Exception:
            pass
        os.replace(path, backup)
    except Exception:
        # If rotation fails, carry on; logging should never break core behavior.
        return


def _write_zwingli_debug_event(event: dict[str, Any]) -> None:
    if not _zwingli_debug_log_enabled():
        return

    payload = dict(event)
    payload.setdefault("ts_ms", int(time.time() * 1000))
    payload.setdefault("pid", int(os.getpid()))

    # Keep the log usable when commands produce large output.
    for key in ("text", "remainder", "prompt", "args", "command", "stdout", "stderr", "output_text", "error"):
        if key in payload:
            payload[key] = _truncate_for_log(payload[key])

    try:
        path = _zwingli_debug_log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        _maybe_rotate_debug_log(path)

        line = json.dumps(payload, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass
