"""Small platform helpers used to keep the codebase cross-platform.

This module is intentionally tiny and stdlib-only. It should be safe to import
from hot paths (e.g. hotkey runners) and during unit tests on any OS.
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    return sys.platform == "darwin"


def pid_is_running(pid: int) -> bool:
    """Return True if `pid` appears to be a running process."""
    if pid <= 0:
        return False

    if not is_windows():
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it.
            return True

    # Windows implementation: OpenProcess + GetExitCodeProcess.
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        ERROR_ACCESS_DENIED = 5

        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE

        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            err = ctypes.get_last_error()
            if err == ERROR_ACCESS_DENIED:
                return True
            return False

        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
    except Exception:
        # Conservative fallback: if we can't determine status, assume not running.
        return False


def supports_af_unix() -> bool:
    """Return True if the runtime supports `socket.AF_UNIX` sockets."""
    try:
        import socket

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            return True
        finally:
            try:
                s.close()
            except Exception:
                pass
    except Exception:
        return False


def getenv_path(name: str) -> Optional[str]:
    raw = (os.environ.get(name) or "").strip()
    return raw or None

