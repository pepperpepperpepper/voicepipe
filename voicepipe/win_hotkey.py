"""Windows hotkey runner for Voicepipe.

This is intentionally stdlib-only. It registers a global hotkey (Alt+F5 by
default) and triggers `voicepipe-fast toggle` behavior in-process.

Run it with `pythonw -m voicepipe.win_hotkey` and start it at login via the
Startup folder or Task Scheduler.
"""

from __future__ import annotations

import os
import threading
from typing import Final

from voicepipe.platform import is_windows


WM_HOTKEY: Final[int] = 0x0312
MOD_ALT: Final[int] = 0x0001
VK_F5: Final[int] = 0x74


def _hotkey_id() -> int:
    return 1


def _log(message: str) -> None:
    # Reuse the fast logger so hotkey runs show up alongside toggle logs.
    try:
        from voicepipe.fast import fast_log

        fast_log(f"[HOTKEY] {message}")
    except Exception:
        pass


def _run_toggle() -> None:
    try:
        from voicepipe.fast import toggle_main

        toggle_main()
    except SystemExit:
        return
    except Exception as e:
        _log(f"toggle failed: {e}")


def _parse_hotkey() -> tuple[int, int]:
    """Return (modifiers, virtual_key)."""
    raw = (os.environ.get("VOICEPIPE_HOTKEY") or "").strip()
    if not raw:
        return MOD_ALT, VK_F5

    normalized = raw.lower().replace(" ", "")
    if normalized in {"alt+f5", "alt-f5"}:
        return MOD_ALT, VK_F5

    raise SystemExit(
        "Unsupported VOICEPIPE_HOTKEY value (only Alt+F5 is supported for now): "
        f"{raw!r}"
    )


def main() -> None:
    if not is_windows():
        raise SystemExit("voicepipe.win_hotkey is Windows-only")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    user32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
    user32.UnregisterHotKey.restype = wintypes.BOOL

    modifiers, vk = _parse_hotkey()
    hotkey_id = _hotkey_id()
    if not user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
        err = ctypes.get_last_error()
        raise SystemExit(
            f"Failed to register hotkey (Alt+F5). Error={err}. "
            "Is something else already using this hotkey?"
        )

    _log("registered Alt+F5")

    msg = wintypes.MSG()
    try:
        while True:
            rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if rc == 0:
                break
            if rc == -1:
                err = ctypes.get_last_error()
                raise SystemExit(f"GetMessageW failed: {err}")
            if msg.message == WM_HOTKEY and int(msg.wParam) == hotkey_id:
                _log("hotkey pressed")
                threading.Thread(target=_run_toggle, daemon=True).start()
    finally:
        try:
            user32.UnregisterHotKey(None, hotkey_id)
        except Exception:
            pass


if __name__ == "__main__":
    main()

