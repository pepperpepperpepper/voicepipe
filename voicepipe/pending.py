"""Single-pending-command storage for the confirm-then-execute flow.

When a verb is configured with `confirm: true`, the original action is held
here until the user says `zwingli yes` (resume) or `zwingli no` (cancel). The
state is intentionally single-slot: a new pending replaces any prior one,
matching the "I changed my mind" voice gesture.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from voicepipe.paths import runtime_app_dir


DEFAULT_TIMEOUT_SECONDS = 60.0
_FILENAME = "pending-command.json"


@dataclass(frozen=True)
class PendingCommand:
    verb: str
    verb_type: str  # "shell", "execute", or "script"
    command: str
    created_at: float
    expires_at: float
    interpreter: Optional[str] = None  # set for verb_type="script"

    def expired(self, *, now: Optional[float] = None) -> bool:
        return float(now if now is not None else time.time()) >= self.expires_at


def pending_path(*, create_dir: bool = False) -> Path:
    base = runtime_app_dir(create=create_dir)
    return base / _FILENAME


def save_pending(pending: PendingCommand) -> Path:
    path = pending_path(create_dir=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(pending), ensure_ascii=False), encoding="utf-8")
    try:
        os.replace(tmp, path)
    except Exception:
        # Best-effort fallback for platforms where replace can fail.
        path.write_text(json.dumps(asdict(pending), ensure_ascii=False), encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    return path


def load_pending(*, now: Optional[float] = None) -> Optional[PendingCommand]:
    """Load the current pending command, or None if absent/corrupt/expired.

    An expired pending file is removed as a side effect so subsequent calls
    don't keep stumbling over it.
    """
    path = pending_path(create_dir=False)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        clear_pending()
        return None
    try:
        raw_interpreter = raw.get("interpreter") if isinstance(raw, dict) else None
        interpreter = str(raw_interpreter) if raw_interpreter else None
        pending = PendingCommand(
            verb=str(raw["verb"]),
            verb_type=str(raw["verb_type"]),
            command=str(raw["command"]),
            created_at=float(raw["created_at"]),
            expires_at=float(raw["expires_at"]),
            interpreter=interpreter,
        )
    except (KeyError, TypeError, ValueError):
        clear_pending()
        return None
    if pending.expired(now=now):
        clear_pending()
        return None
    return pending


def clear_pending() -> None:
    path = pending_path(create_dir=False)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def make_pending(
    *,
    verb: str,
    verb_type: str,
    command: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    now: Optional[float] = None,
    interpreter: Optional[str] = None,
) -> PendingCommand:
    created = float(now if now is not None else time.time())
    return PendingCommand(
        verb=verb,
        verb_type=verb_type,
        command=command,
        created_at=created,
        expires_at=created + float(timeout_seconds),
        interpreter=interpreter,
    )
