"""Small cross-platform file lock helpers.

Used by hotkey-style entrypoints (e.g. `voicepipe-fast toggle`) to avoid running
multiple instances concurrently.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from voicepipe.platform import is_windows


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


def _lock_info(path: Path) -> Tuple[Optional[int], str]:
    """Best-effort (pid, raw_first_line) for messaging."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None, ""
    first = (raw.splitlines()[0].strip() if raw else "").strip()
    try:
        return int(first), first
    except Exception:
        return None, first


def _try_lock_fd(fd: int) -> None:
    """Lock 1 byte at offset 0 (non-blocking)."""
    os.lseek(fd, 0, os.SEEK_SET)
    if is_windows():
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_fd(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if is_windows():
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@dataclass
class PidFileLock:
    """A tiny PID-file lock based on OS-level file locking.

    Implementation notes:
    - Uses `fcntl.flock` on Unix, `msvcrt.locking` on Windows.
    - Keeps the FD open for the duration of the lock (releases on process exit).
    - Writes the PID to the lock file for debugging only.
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

        flags = os.O_CREAT | os.O_RDWR
        mode = 0o600

        try:
            fd = os.open(str(self.path), flags, mode)
        except Exception as e:
            raise LockHeld(f"could not open lock file {self.path}: {e}") from e

        try:
            try:
                _try_lock_fd(fd)
            except Exception as e:
                pid, first = _lock_info(self.path)
                detail = f" pid={pid}" if pid is not None else (f" first_line={first!r}" if first else "")
                raise LockHeld(f"lock is held: {self.path}{detail}") from e

            self.fd = fd

            # Best-effort: store PID for debugging.
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"{os.getpid()}\n".encode("utf-8", errors="replace"))
            except Exception:
                pass
        except Exception:
            try:
                os.close(fd)
            except Exception:
                pass
            raise

    def release(self) -> None:
        fd = self.fd
        self.fd = None
        if fd is not None:
            try:
                _unlock_fd(fd)
            except Exception:
                pass
            try:
                os.close(fd)
            except Exception:
                pass
        try:
            self.path.unlink()
        except Exception:
            pass
