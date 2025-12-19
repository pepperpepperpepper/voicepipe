"""Shared helpers for typing text into the active application."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional


def get_active_window_id() -> Optional[str]:
    """Best-effort capture of the active window for later typing (X11 only)."""
    xdotool_path = shutil.which("xdotool")
    if not xdotool_path:
        return None
    try:
        result = subprocess.run(
            [xdotool_path, "getactivewindow"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
        if result.returncode != 0:
            return None
        win = (result.stdout or "").strip()
        return win or None
    except Exception:
        return None


def type_text(text: str, *, window_id: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Type text using the best available backend.

    Returns:
      (ok, error_message)
    """
    if not text:
        return True, None

    xdotool_path = shutil.which("xdotool")
    if xdotool_path and os.environ.get("DISPLAY"):
        cmd = [xdotool_path, "type", "--clearmodifiers"]
        if window_id:
            cmd += ["--window", str(window_id)]
        cmd += ["--", text]
        try:
            timeout_s = max(2.0, min(30.0, len(text) / 20.0))
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                return False, err or f"xdotool failed (rc={result.returncode})"
            return True, None
        except subprocess.TimeoutExpired:
            return False, "xdotool timed out"
        except Exception as e:
            return False, f"xdotool error: {e}"

    # Wayland best-effort support.
    wtype_path = shutil.which("wtype")
    if wtype_path and os.environ.get("WAYLAND_DISPLAY"):
        try:
            result = subprocess.run(
                [wtype_path, text],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if result.returncode == 0:
                return True, None

            # Some variants may read from stdin instead of argv.
            result = subprocess.run(
                [wtype_path],
                input=text,
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if result.returncode == 0:
                return True, None

            err = (result.stderr or "").strip()
            return False, err or f"wtype failed (rc={result.returncode})"
        except subprocess.TimeoutExpired:
            return False, "wtype timed out"
        except Exception as e:
            return False, f"wtype error: {e}"

    return False, "No typing backend available (xdotool for X11, wtype for Wayland)"

