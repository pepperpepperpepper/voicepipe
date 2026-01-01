"""Small cross-platform file lock helpers.

Used by hotkey-style entrypoints (e.g. `voicepipe-fast toggle`) to avoid running
multiple instances concurrently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from voicepipe.platform import pid_is_running


class LockHeld(RuntimeError):
    pass


def _read_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return int(raw.splitlines()[0].strip())
    except Exception:
        return None


@dataclass
class PidFileLock:
    """A tiny PID-file lock based on atomic file creation.

    Implementation notes:
    - Uses `os.open(..., O_CREAT|O_EXCL)` so it works on Windows and Unix.
    - Keeps the FD open for the duration of the lock.
    - Attempts a single stale-lock recovery when the stored PID is not running.
    """

    path: Path
    fd: int | None = None

    def __enter__(self) -> "PidFileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.release()

    def acquire(self) -> None:
        if self.fd is not None:
            return

        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        mode = 0o600

        for attempt in range(2):
            try:
                fd = os.open(str(self.path), flags, mode)
            except FileExistsError:
                pid = _read_pid(self.path)
                if pid is not None and not pid_is_running(int(pid)):
                    try:
                        self.path.unlink()
                    except Exception:
                        pass
                    continue
                raise LockHeld(f"lock is held: {self.path}")

            self.fd = fd
            try:
                os.write(fd, f"{os.getpid()}\n".encode("utf-8", errors="replace"))
            except Exception:
                # Best-effort; lock ownership doesn't depend on PID contents.
                pass
            return

        raise LockHeld(f"lock is held: {self.path}")

    def release(self) -> None:
        fd = self.fd
        self.fd = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            self.path.unlink()
        except Exception:
            pass

