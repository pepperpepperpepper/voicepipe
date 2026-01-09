"""Windows hotkey runner for Voicepipe.

This is intentionally stdlib-only. It registers a global hotkey (Alt+F5 by
default) and triggers `voicepipe-fast toggle` behavior in-process.

Run it with `pythonw -m voicepipe.win_hotkey` and start it at login via the
Startup folder or Task Scheduler.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Final

from voicepipe.platform import is_windows


WM_HOTKEY: Final[int] = 0x0312
MOD_ALT: Final[int] = 0x0001
MOD_NOREPEAT: Final[int] = 0x4000
VK_F5: Final[int] = 0x74
VK_MENU: Final[int] = 0x12  # Alt

WH_KEYBOARD_LL: Final[int] = 13
WM_KEYDOWN: Final[int] = 0x0100
WM_SYSKEYDOWN: Final[int] = 0x0104
WM_KEYUP: Final[int] = 0x0101
WM_SYSKEYUP: Final[int] = 0x0105

_TOGGLE_FN = None
_HOOK_HANDLE = None
_HOOK_PROC = None


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
        global _TOGGLE_FN
        if _TOGGLE_FN is None:
            from voicepipe.fast import toggle_inprocess_main

            _TOGGLE_FN = toggle_inprocess_main

        _TOGGLE_FN()
    except SystemExit:
        return
    except Exception as e:
        _log(f"toggle failed: {e}")


def _prewarm_audio() -> None:
    try:
        from voicepipe.audio import resolve_audio_input_for_recording
        from voicepipe.config import get_audio_channels, get_audio_sample_rate

        res = resolve_audio_input_for_recording(
            preferred_samplerate=get_audio_sample_rate(),
            preferred_channels=get_audio_channels(),
        )
        sel = res.selection
        _log(
            "audio prewarmed: "
            f"device={sel.device_index} samplerate={sel.samplerate} channels={sel.channels} "
            f"source={getattr(res, 'source', '')}"
        )
    except Exception as e:
        _log(f"audio prewarm failed: {e}")


def _prewarm_fast() -> None:
    try:
        global _TOGGLE_FN
        if _TOGGLE_FN is None:
            from voicepipe.fast import toggle_inprocess_main

            _TOGGLE_FN = toggle_inprocess_main
    except Exception as e:
        _log(f"fast prewarm failed: {e}")


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

    # Emit a small diagnostic header early so we can debug Task Scheduler /
    # environment issues from logs without an interactive console.
    try:
        import sys

        import voicepipe  # noqa: F401

        from voicepipe.config import env_file_path
        from voicepipe.paths import runtime_app_dir

        env_path = env_file_path()
        try:
            env_exists = bool(env_path.exists())
        except Exception:
            env_exists = False
        _log(f"python={sys.executable}")
        _log(f"voicepipe={getattr(voicepipe, '__file__', None)}")
        _log(f"cwd={os.getcwd()}")
        _log(f"env_file={env_path} exists={env_exists}")
        _log(
            "env: "
            f"LOCALAPPDATA={'set' if os.environ.get('LOCALAPPDATA') else 'missing'} "
            f"TEMP={'set' if os.environ.get('TEMP') else 'missing'} "
            f"USERPROFILE={'set' if os.environ.get('USERPROFILE') else 'missing'}"
        )
        _log(f"runtime_dir={runtime_app_dir(create=False)}")
    except Exception as e:
        _log(f"diagnostics failed: {e}")

    import ctypes
    from ctypes import wintypes

    # Some Python builds omit less-common wintypes aliases. Keep our usage
    # defensive so the hotkey runner still works.
    LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
    HHOOK = getattr(wintypes, "HHOOK", wintypes.HANDLE)
    HINSTANCE = getattr(wintypes, "HINSTANCE", wintypes.HANDLE)
    HMODULE = getattr(wintypes, "HMODULE", wintypes.HANDLE)

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    user32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
    user32.UnregisterHotKey.restype = wintypes.BOOL

    user32.GetAsyncKeyState.argtypes = [wintypes.INT]
    user32.GetAsyncKeyState.restype = wintypes.SHORT

    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = HHOOK
    user32.UnhookWindowsHookEx.argtypes = [HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.CallNextHookEx.restype = LRESULT

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = HMODULE

    try:
        modifiers, vk = _parse_hotkey()
    except SystemExit as e:
        _log(str(e))
        raise
    hotkey_id = _hotkey_id()
    registered = False
    ignore_wm_hotkey_until = 0.0

    def _install_low_level_hook() -> None:
        global _HOOK_HANDLE
        global _HOOK_PROC

        nonlocal ignore_wm_hotkey_until

        # Prevent key-repeat from firing multiple toggles while the user holds
        # Alt+F5 down. Some environments emit key-repeat as down/up pairs, so we
        # consult GetAsyncKeyState before clearing the latch.
        hotkey_latched = False

        ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def _proc(nCode: int, wParam: int, lParam: int) -> int:  # type: ignore[override]
            nonlocal hotkey_latched
            nonlocal ignore_wm_hotkey_until

            msg = int(wParam)
            if nCode == 0 and msg in (WM_KEYUP, WM_SYSKEYUP):
                try:
                    info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if int(info.vkCode) in (int(vk), VK_MENU):
                        # Clear the latch only when the hotkey key is *actually*
                        # up. This avoids spurious repeats from drivers that
                        # generate synthetic up/down pairs while the key is held.
                        f5_down = bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)
                        if not f5_down:
                            hotkey_latched = False
                except Exception:
                    hotkey_latched = False

            if nCode == 0 and msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                try:
                    info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if int(info.vkCode) == int(vk):
                        alt_down = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                        if alt_down:
                            if not hotkey_latched:
                                hotkey_latched = True
                                # If RegisterHotKey is working, WM_HOTKEY will
                                # fire too. Ignore it so we only toggle once.
                                ignore_wm_hotkey_until = time.monotonic() + 0.75
                                _log("hotkey pressed (hook)")
                                threading.Thread(target=_run_toggle, daemon=True).start()
                            # Swallow the hotkey only when RegisterHotKey failed
                            # (otherwise the system will suppress it for us).
                            if not registered:
                                return 1
                except Exception:
                    pass

            return int(user32.CallNextHookEx(_HOOK_HANDLE, nCode, wParam, lParam))

        _HOOK_PROC = HOOKPROC(_proc)
        hmod = kernel32.GetModuleHandleW(None)
        _HOOK_HANDLE = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _HOOK_PROC, hmod, 0)
        if not _HOOK_HANDLE:
            err = ctypes.get_last_error()
            raise SystemExit(f"Failed to install keyboard hook (Alt+F5). Error={err}")

    if user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
        registered = True
        _log("registered Alt+F5 (RegisterHotKey)")
    else:
        # MOD_NOREPEAT is not supported on very old systems; try again without it.
        if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
            registered = True
            _log("registered Alt+F5 (RegisterHotKey)")
        else:
            err = ctypes.get_last_error()
            _log(
                "RegisterHotKey failed for Alt+F5 (will fall back to keyboard hook). "
                f"Error={err}"
            )
    # Always install the low-level hook as a reliability fallback: some
    # environments intercept hotkeys in a way that prevents WM_HOTKEY delivery.
    try:
        _install_low_level_hook()
        _log("installed Alt+F5 keyboard hook")
    except SystemExit as e:
        # Best-effort: keep running if RegisterHotKey succeeded.
        _log(str(e))
        if not registered:
            raise

    threading.Thread(target=_prewarm_audio, daemon=True).start()
    threading.Thread(target=_prewarm_fast, daemon=True).start()

    msg = wintypes.MSG()
    try:
        while True:
            rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if rc == 0:
                break
            if rc == -1:
                err = ctypes.get_last_error()
                raise SystemExit(f"GetMessageW failed: {err}")
            if registered and msg.message == WM_HOTKEY and int(msg.wParam) == hotkey_id:
                if time.monotonic() < ignore_wm_hotkey_until:
                    continue
                _log("hotkey pressed (wm_hotkey)")
                threading.Thread(target=_run_toggle, daemon=True).start()
    finally:
        if registered:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        try:
            if _HOOK_HANDLE:
                user32.UnhookWindowsHookEx(_HOOK_HANDLE)
        except Exception:
            pass


if __name__ == "__main__":
    main()
