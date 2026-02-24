"""Clipboard helper utilities.

Voicepipe primarily operates in terminal and hotkey workflows, but users often
want to recover/transmit dictation via the OS clipboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from voicepipe.platform import is_macos, is_windows


def copy_to_clipboard(text: str) -> tuple[bool, str | None]:
    """Copy text to the OS clipboard (best-effort).

    Returns (ok, error_message).
    """
    payload = text or ""

    try:
        if is_macos():
            if not shutil.which("pbcopy"):
                return False, "pbcopy not found"
            subprocess.run(["pbcopy"], input=payload, text=True, check=True)
            return True, None

        if is_windows():
            # `clip` is widely available on modern Windows installations.
            if not shutil.which("clip"):
                return False, "clip not found"
            subprocess.run(["clip"], input=payload, text=True, check=True)
            return True, None

        # Linux / other Unix:
        wayland = bool((os.environ.get("WAYLAND_DISPLAY") or "").strip())
        x11 = bool((os.environ.get("DISPLAY") or "").strip())

        if wayland and shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=payload, text=True, check=True)
            return True, None

        if x11 and shutil.which("xclip"):
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=payload,
                text=True,
                check=True,
            )
            return True, None

        if x11 and shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=payload, text=True, check=True)
            return True, None

        # Fall back to attempting tools even if env vars aren't set; provide a
        # clear error if they fail.
        if shutil.which("wl-copy"):
            subprocess.run(["wl-copy"], input=payload, text=True, check=True)
            return True, None
        if shutil.which("xclip"):
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=payload,
                text=True,
                check=True,
            )
            return True, None
        if shutil.which("xsel"):
            subprocess.run(["xsel", "--clipboard", "--input"], input=payload, text=True, check=True)
            return True, None

        hint = "Install wl-clipboard (wl-copy) for Wayland or xclip/xsel for X11."
        return False, f"No clipboard tool found. {hint}"
    except subprocess.CalledProcessError as e:
        return False, f"Clipboard command failed (exit {e.returncode})"
    except Exception as e:
        return False, str(e)

