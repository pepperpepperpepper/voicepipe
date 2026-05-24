"""Shared helpers for typing text into the active application."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Optional, Tuple

from voicepipe.platform import is_macos, is_windows


def get_active_window_id() -> Optional[str]:
    """Best-effort capture of the active window for later typing."""
    if is_windows():
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.GetForegroundWindow.argtypes = []
            user32.GetForegroundWindow.restype = wintypes.HWND
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None
            return str(int(hwnd))
        except Exception:
            return None

    # X11 best-effort.
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


TypingBackendName = Literal["wtype", "xdotool", "sendinput", "osascript", "none", "unavailable"]
SessionType = Literal["wayland", "x11", "windows", "macos", "unknown"]


@dataclass(frozen=True)
class TypingBackend:
    name: TypingBackendName
    supports_window_id: bool
    path: Optional[str]
    error: Optional[str]
    session_type: SessionType
    reason: str


def _detect_session_type(env: Mapping[str, str]) -> Tuple[SessionType, str]:
    """Detect the display session type using env signals only.

    We avoid spawning subprocesses (e.g. loginctl) because this runs in hotkey
    paths where latency matters.
    """
    if is_windows():
        return "windows", "win32"

    if is_macos():
        return "macos", "darwin"

    session_type = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if session_type in ("wayland", "x11"):
        return session_type, f"XDG_SESSION_TYPE={session_type}"

    if env.get("WAYLAND_DISPLAY"):
        return "wayland", "WAYLAND_DISPLAY set"

    if env.get("DISPLAY"):
        return "x11", "DISPLAY set"

    return "unknown", "missing DISPLAY/WAYLAND_DISPLAY/XDG_SESSION_TYPE"


_CACHED_DEFAULT_BACKEND: TypingBackend | None = None
_CACHED_DEFAULT_BACKEND_KEY: tuple[object, str, str, str, str, str] | None = None


def _resolve_typing_backend_cached() -> TypingBackend:
    """Resolve the default typing backend with simple per-process caching."""

    global _CACHED_DEFAULT_BACKEND
    global _CACHED_DEFAULT_BACKEND_KEY

    env = os.environ
    # Cache key includes values that affect selection + which() results.
    key = (
        shutil.which,
        (env.get("VOICEPIPE_TYPE_BACKEND") or "").strip().lower(),
        (env.get("XDG_SESSION_TYPE") or "").strip().lower(),
        env.get("WAYLAND_DISPLAY") or "",
        env.get("DISPLAY") or "",
        env.get("PATH") or "",
    )
    if _CACHED_DEFAULT_BACKEND is not None and _CACHED_DEFAULT_BACKEND_KEY == key:
        return _CACHED_DEFAULT_BACKEND

    backend = resolve_typing_backend(env=env, which=shutil.which)
    _CACHED_DEFAULT_BACKEND = backend
    _CACHED_DEFAULT_BACKEND_KEY = key
    return backend


def resolve_typing_backend(
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> TypingBackend:
    """Resolve the typing backend once based on env + available binaries.

    Config:
      VOICEPIPE_TYPE_BACKEND=auto|wayland|x11|wtype|xdotool|osascript|sendinput|none
    """

    if env is None:
        env = os.environ
    if which is None:
        which = shutil.which

    override_raw = (env.get("VOICEPIPE_TYPE_BACKEND") or "").strip().lower()
    override = override_raw or "auto"

    # Friendlier aliases (so users can think in terms of display server rather than tool name).
    aliases = {
        "wayland": "wtype",
        "wl": "wtype",
        "x11": "xdotool",
        "x": "xdotool",
        "macos": "osascript",
        "mac": "osascript",
        "osx": "osascript",
        "darwin": "osascript",
        "disable": "none",
        "disabled": "none",
        "off": "none",
    }
    override = aliases.get(override, override)

    wtype_path: str | None = None
    wtype_checked = False
    xdotool_path: str | None = None
    xdotool_checked = False

    def which_wtype() -> str | None:
        nonlocal wtype_checked, wtype_path
        if not wtype_checked:
            wtype_path = which("wtype")
            wtype_checked = True
        return wtype_path

    def which_xdotool() -> str | None:
        nonlocal xdotool_checked, xdotool_path
        if not xdotool_checked:
            xdotool_path = which("xdotool")
            xdotool_checked = True
        return xdotool_path

    if override == "none":
        return TypingBackend(
            name="none",
            supports_window_id=False,
            path=None,
            error="Typing disabled (VOICEPIPE_TYPE_BACKEND=none)",
            session_type=_detect_session_type(env)[0],
            reason="explicit override: none",
        )

    if is_windows():
        if override in ("auto", "sendinput"):
            return TypingBackend(
                name="sendinput",
                supports_window_id=True,
                path=None,
                error=None,
                session_type="windows",
                reason=f"{'explicit' if override != 'auto' else 'auto'}: sendinput",
            )
        return TypingBackend(
            name="unavailable",
            supports_window_id=False,
            path=None,
            error=(
                f"Unknown VOICEPIPE_TYPE_BACKEND={override_raw!r} "
                "(expected: auto|sendinput|none)"
            ),
            session_type="windows",
            reason="invalid override",
        )

    if is_macos():
        if override in ("auto", "osascript"):
            path = which("osascript")
            if not path:
                return TypingBackend(
                    name="unavailable",
                    supports_window_id=False,
                    path=None,
                    error="osascript not found (expected on macOS at /usr/bin/osascript)",
                    session_type="macos",
                    reason=(
                        f"{'explicit' if override != 'auto' else 'auto'}: osascript "
                        "(missing binary)"
                    ),
                )
            return TypingBackend(
                name="osascript",
                supports_window_id=False,
                path=path,
                error=None,
                session_type="macos",
                reason=f"{'explicit' if override != 'auto' else 'auto'}: osascript",
            )
        return TypingBackend(
            name="unavailable",
            supports_window_id=False,
            path=None,
            error=(
                f"Unknown VOICEPIPE_TYPE_BACKEND={override_raw!r} "
                "(expected: auto|osascript|none)"
            ),
            session_type="macos",
            reason="invalid override",
        )

    if override in ("wtype", "xdotool"):
        if override == "wtype":
            path = which_wtype()
            if not path:
                return TypingBackend(
                    name="unavailable",
                    supports_window_id=False,
                    path=None,
                    error="wtype not found (install wtype or set VOICEPIPE_TYPE_BACKEND=xdotool|auto)",
                    session_type=_detect_session_type(env)[0],
                    reason="explicit override: wtype (missing binary)",
                )
            return TypingBackend(
                name="wtype",
                supports_window_id=False,
                path=path,
                error=None,
                session_type=_detect_session_type(env)[0],
                reason="explicit override: wtype",
            )
        path = which_xdotool()
        if not path:
            return TypingBackend(
                name="unavailable",
                supports_window_id=False,
                path=None,
                error="xdotool not found (install xdotool or set VOICEPIPE_TYPE_BACKEND=wtype|auto)",
                session_type=_detect_session_type(env)[0],
                reason="explicit override: xdotool (missing binary)",
            )
        return TypingBackend(
            name="xdotool",
            supports_window_id=True,
            path=path,
            error=None,
            session_type=_detect_session_type(env)[0],
            reason="explicit override: xdotool",
        )

    if override != "auto":
        return TypingBackend(
            name="unavailable",
            supports_window_id=False,
            path=None,
            error=(
                f"Unknown VOICEPIPE_TYPE_BACKEND={override_raw!r} "
                "(expected: auto|wayland|x11|wtype|xdotool|none)"
            ),
            session_type=_detect_session_type(env)[0],
            reason="invalid override",
        )

    session, session_reason = _detect_session_type(env)

    # Wayland-first: many Wayland sessions also set DISPLAY (Xwayland), which
    # would incorrectly prefer xdotool.
    if session == "wayland":
        path = which_wtype()
        if path:
            return TypingBackend(
                name="wtype",
                supports_window_id=False,
                path=path,
                error=None,
                session_type="wayland",
                reason=f"auto: {session_reason}; selected wtype",
            )
        return TypingBackend(
            name="unavailable",
            supports_window_id=False,
            path=None,
            error=(
                "Wayland session detected but wtype is not available. "
                "Install wtype, or set VOICEPIPE_TYPE_BACKEND=xdotool "
                "(X11/Xwayland only)."
            ),
            session_type="wayland",
            reason=f"auto: {session_reason}; wtype missing",
        )

    if session == "x11":
        path = which_xdotool()
        if path:
            return TypingBackend(
                name="xdotool",
                supports_window_id=True,
                path=path,
                error=None,
                session_type="x11",
                reason=f"auto: {session_reason}; selected xdotool",
            )
        return TypingBackend(
            name="unavailable",
            supports_window_id=False,
            path=None,
            error="X11 session detected but xdotool is not available. Install xdotool.",
            session_type="x11",
            reason=f"auto: {session_reason}; xdotool missing",
        )

    return TypingBackend(
        name="unavailable",
        supports_window_id=False,
        path=None,
        error=(
            "No display session detected (missing DISPLAY/WAYLAND_DISPLAY). "
            "Typing requires an interactive X11 or Wayland session."
        ),
        session_type="unknown",
        reason=f"auto: {session_reason}",
    )


def _sendinput_focus_window(window_id: str) -> None:
    if not window_id:
        return
    try:
        hwnd = int(str(window_id).strip(), 0)
    except Exception:
        return
    if hwnd <= 0:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.SetForegroundWindow(wintypes.HWND(hwnd))
    except Exception:
        pass


def _sendinput_type_text(text: str, *, window_id: Optional[str]) -> tuple[bool, Optional[str]]:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)

        # Basic interactive-session check.
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd_foreground = user32.GetForegroundWindow()
        if not hwnd_foreground:
            return False, "No interactive desktop session available (no foreground window)"

        if window_id:
            _sendinput_focus_window(window_id)
            hwnd_foreground = user32.GetForegroundWindow() or hwnd_foreground

        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        VK_ESCAPE = 0x1B
        VK_RETURN = 0x0D

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
        user32.GetGUIThreadInfo.restype = wintypes.BOOL

        def _send_inputs(inputs: list[INPUT]) -> bool:
            if not inputs:
                return True
            arr = (INPUT * len(inputs))(*inputs)
            sent = int(user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT)))
            if sent == len(arr):
                return True
            # Preserve the most useful signal for debugging.
            err = ctypes.get_last_error()
            raise RuntimeError(f"SendInput sent {sent}/{len(arr)} events (GetLastError={err})")

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", RECT),
            ]

        user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]

        def _vk_input(vk: int, *, keyup: bool = False) -> INPUT:
            flags = KEYEVENTF_KEYUP if keyup else 0
            return INPUT(
                type=INPUT_KEYBOARD,
                union=_INPUT_UNION(
                    ki=KEYBDINPUT(wVk=wintypes.WORD(vk), wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
                ),
            )

        def _clear_modifiers() -> None:
            # When invoked from an Alt-based hotkey, the target app can end up
            # with its menu activated, and modifier state can interfere with
            # subsequent input. Best-effort release common modifiers before we
            # type.
            VK_SHIFT = 0x10
            VK_CONTROL = 0x11
            VK_MENU = 0x12
            VK_LSHIFT = 0xA0
            VK_RSHIFT = 0xA1
            VK_LCONTROL = 0xA2
            VK_RCONTROL = 0xA3
            VK_LMENU = 0xA4
            VK_RMENU = 0xA5
            VK_LWIN = 0x5B
            VK_RWIN = 0x5C

            inputs = [
                _vk_input(VK_LSHIFT, keyup=True),
                _vk_input(VK_RSHIFT, keyup=True),
                _vk_input(VK_SHIFT, keyup=True),
                _vk_input(VK_LCONTROL, keyup=True),
                _vk_input(VK_RCONTROL, keyup=True),
                _vk_input(VK_CONTROL, keyup=True),
                _vk_input(VK_LMENU, keyup=True),
                _vk_input(VK_RMENU, keyup=True),
                _vk_input(VK_MENU, keyup=True),
                _vk_input(VK_LWIN, keyup=True),
                _vk_input(VK_RWIN, keyup=True),
            ]
            try:
                _send_inputs(inputs)
            except Exception:
                pass

        def _close_menu_if_active(hwnd: wintypes.HWND) -> None:
            try:
                pid = wintypes.DWORD()
                tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if not tid:
                    return
                info = GUITHREADINFO()
                info.cbSize = ctypes.sizeof(GUITHREADINFO)
                if not user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
                    return
                if not info.hwndMenuOwner:
                    return
            except Exception:
                return

            try:
                _send_inputs([_vk_input(VK_ESCAPE), _vk_input(VK_ESCAPE, keyup=True)])
                time.sleep(0.01)
            except Exception:
                pass

        _clear_modifiers()
        _close_menu_if_active(hwnd_foreground)

        # Convert to UTF-16 code units so we can handle surrogate pairs.
        units = text.replace("\r\n", "\n").encode("utf-16-le", errors="surrogatepass")
        code_units = [int.from_bytes(units[i : i + 2], "little") for i in range(0, len(units), 2)]

        batch: list[INPUT] = []

        def _flush() -> bool:
            nonlocal batch
            ok = _send_inputs(batch)
            batch = []
            return ok

        for cu in code_units:
            if cu == 0x000A:  # \n
                # Prefer a VK_RETURN for predictable newlines.
                batch.append(INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0))))
                batch.append(INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=VK_RETURN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))
            else:
                batch.append(INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=cu, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0))))
                batch.append(INPUT(type=INPUT_KEYBOARD, union=_INPUT_UNION(ki=KEYBDINPUT(wVk=0, wScan=cu, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0))))

            # Flush periodically to avoid huge SendInput calls.
            if len(batch) >= 64:
                if not _flush():
                    return False, "SendInput failed"

        if not _flush():
            return False, "SendInput failed"

        return True, None
    except Exception as e:
        return False, f"sendinput error: {e}"

def _osascript_type_text(
    osascript_path: str,
    text: str,
) -> tuple[bool, Optional[str]]:
    # Avoid hanging when invoked from non-interactive remote sessions.
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") or os.environ.get("SSH_CLIENT"):
        return False, "osascript typing requires an interactive macOS desktop session (SSH detected)"

    # AppleScript's `paragraphs` splits on carriage returns, so normalize and
    # convert to `\r` before passing as an argv item to `osascript`.
    apple_text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r")
    script = """on run argv
    if (count of argv) is 0 then return
    set t to item 1 of argv
    set n to count of paragraphs of t
    tell application "System Events"
        repeat with i from 1 to n
            set lineText to paragraph i of t
            keystroke lineText
            if i is not n then
                key code 36
            end if
        end repeat
    end tell
end run
"""
    try:
        timeout_s = max(2.0, min(30.0, len(text) / 20.0))
        result = subprocess.run(
            [osascript_path, "-", apple_text],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if result.returncode == 0:
            return True, None
        err = (result.stderr or "").strip()
        out = (result.stdout or "").strip()
        detail = err or out
        return False, detail or f"osascript failed (rc={result.returncode})"
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    except Exception as e:
        return False, f"osascript error: {e}"


def type_text(
    text: str,
    *,
    window_id: Optional[str] = None,
    backend: TypingBackend | None = None,
) -> tuple[bool, Optional[str]]:
    """Type text using the best available backend.

    Returns:
      (ok, error_message)
    """
    if not text:
        return True, None

    backend = _resolve_typing_backend_cached() if backend is None else backend
    if backend.name in ("none", "unavailable"):
        return False, backend.error or "Typing unavailable"

    if backend.name == "sendinput":
        return _sendinput_type_text(text, window_id=window_id)

    if backend.name == "osascript":
        return _osascript_type_text(backend.path or "osascript", text)

    if backend.name == "xdotool":
        cmd = [backend.path or "xdotool", "type", "--clearmodifiers"]
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

    if backend.name == "wtype":
        try:
            result = subprocess.run(
                [backend.path or "wtype", text],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if result.returncode == 0:
                return True, None

            # Some variants may read from stdin instead of argv.
            result = subprocess.run(
                [backend.path or "wtype"],
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

    return False, backend.error or "No typing backend available"


def press_enter(
    *,
    window_id: Optional[str] = None,
    backend: TypingBackend | None = None,
) -> tuple[bool, Optional[str]]:
    """Press the Enter/Return key using the best available backend.

    Prefer this over typing a literal newline when the target expects a real
    keypress event (some shells/apps distinguish Enter from inserting '\n').
    """
    backend = _resolve_typing_backend_cached() if backend is None else backend
    if backend.name in ("none", "unavailable"):
        return False, backend.error or "Typing unavailable"

    if backend.name == "sendinput":
        # `type_text` already maps '\n' to VK_RETURN for predictable behavior.
        return type_text("\n", window_id=window_id, backend=backend)

    if backend.name == "osascript":
        # Avoid hanging when invoked from non-interactive remote sessions.
        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") or os.environ.get("SSH_CLIENT"):
            return False, "osascript typing requires an interactive macOS desktop session (SSH detected)"
        try:
            script = 'tell application "System Events" to key code 36\n'
            result = subprocess.run(
                [backend.path or "osascript", "-"],
                input=script,
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if result.returncode == 0:
                return True, None
            err = (result.stderr or "").strip()
            out = (result.stdout or "").strip()
            detail = err or out
            return False, detail or f"osascript failed (rc={result.returncode})"
        except subprocess.TimeoutExpired:
            return False, "osascript timed out"
        except Exception as e:
            return False, f"osascript error: {e}"

    if backend.name == "xdotool":
        cmd = [backend.path or "xdotool", "key", "--clearmodifiers"]
        if window_id:
            cmd += ["--window", str(window_id)]
        cmd += ["Return"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.0,
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

    if backend.name == "wtype":
        # Wayland: send a real Return keysym press/release instead of a newline
        # character. Window targeting is compositor-controlled.
        cmd = [backend.path or "wtype", "-P", "Return", "-p", "Return"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.0,
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

    return False, backend.error or "No typing backend available"


def press_keys(
    chords: list[dict[str, object]],
    *,
    window_id: Optional[str] = None,
    backend: TypingBackend | None = None,
) -> tuple[bool, Optional[str]]:
    """Press a sequence of key chords.

    Each chord is a dict like: {"key": "up", "mods": ["ctrl", "shift"]}.
    """
    backend = _resolve_typing_backend_cached() if backend is None else backend
    if backend.name in ("none", "unavailable"):
        return False, backend.error or "Typing unavailable"

    def _iter_chords() -> list[tuple[str, list[str]]]:
        out: list[tuple[str, list[str]]] = []
        for raw in chords or []:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip().lower()
            if not key:
                continue
            mods: list[str] = []
            raw_mods = raw.get("mods")
            if isinstance(raw_mods, list):
                for m in raw_mods:
                    cleaned = str(m or "").strip().lower()
                    if cleaned:
                        mods.append(cleaned)
            out.append((key, mods))
        return out

    resolved = _iter_chords()
    if not resolved:
        return True, None

    def _map_key(key: str) -> str:
        special = {
            "up": "Up",
            "down": "Down",
            "left": "Left",
            "right": "Right",
            "enter": "Return",
            "tab": "Tab",
            "esc": "Escape",
            "backspace": "BackSpace",
            "delete": "Delete",
            "home": "Home",
            "end": "End",
            "pageup": "Prior",
            "pagedown": "Next",
            "space": "space",
        }
        if key in special:
            return special[key]
        if key.startswith("f") and key[1:].isdigit():
            try:
                n = int(key[1:])
            except Exception:
                n = 0
            if 1 <= n <= 24:
                return f"F{n}"
        return key

    if backend.name == "xdotool":
        specs: list[str] = []
        mod_map = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "meta": "meta",
            "super": "super",
            "cmd": "super",
        }
        for key, mods in resolved:
            mapped_mods = [mod_map.get(m, m) for m in mods if m]
            keysym = _map_key(key)
            spec = "+".join([*mapped_mods, keysym]) if mapped_mods else keysym
            specs.append(spec)
        if not specs:
            return True, None
        cmd = [backend.path or "xdotool", "key", "--clearmodifiers"]
        if window_id:
            cmd += ["--window", str(window_id)]
        cmd += specs
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2.0,
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

    if backend.name == "wtype":
        mod_map = {
            "ctrl": "ctrl",
            "control": "ctrl",
            "shift": "shift",
            "alt": "alt",
            "meta": "logo",
            "super": "logo",
            "cmd": "logo",
        }
        argv: list[str] = [backend.path or "wtype"]
        for key, mods in resolved:
            mapped_mods = [mod_map.get(m, m) for m in mods if m]
            for m in mapped_mods:
                argv += ["-M", m]
            keysym = _map_key(key)
            if len(key) == 1 and key.isalnum():
                argv.append(key)
            else:
                argv += ["-P", keysym, "-p", keysym]
            for m in reversed(mapped_mods):
                argv += ["-m", m]
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=2.0,
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

    if backend.name == "osascript":
        if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") or os.environ.get("SSH_CLIENT"):
            return False, "osascript typing requires an interactive macOS desktop session (SSH detected)"

        mod_map = {
            "ctrl": "control down",
            "control": "control down",
            "shift": "shift down",
            "alt": "option down",
            "meta": "command down",
            "super": "command down",
            "cmd": "command down",
        }
        key_codes = {
            "Up": 126,
            "Down": 125,
            "Left": 123,
            "Right": 124,
            "Return": 36,
            "Tab": 48,
            "Escape": 53,
            "BackSpace": 51,
            # Forward delete.
            "Delete": 117,
        }

        def _using_clause(mods: list[str]) -> str:
            mapped = [mod_map.get(m, "") for m in mods]
            mapped = [m for m in mapped if m]
            if not mapped:
                return ""
            return " using {" + ", ".join(mapped) + "}"

        lines: list[str] = ['tell application "System Events"']
        for key, mods in resolved:
            keysym = _map_key(key)
            using = _using_clause(mods)
            if keysym in key_codes:
                lines.append(f"    key code {int(key_codes[keysym])}{using}")
            elif len(key) == 1 and key.isprintable():
                safe = key.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    keystroke "{safe}"{using}')
            elif keysym.lower() == "space":
                lines.append(f'    keystroke " "{using}')
            else:
                # Fallback: try a keystroke of the keysym string (best-effort).
                safe = keysym.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'    keystroke "{safe}"{using}')
        lines.append("end tell")
        script = "\n".join(lines) + "\n"
        try:
            result = subprocess.run(
                [backend.path or "osascript", "-"],
                input=script,
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if result.returncode == 0:
                return True, None
            err = (result.stderr or "").strip()
            out = (result.stdout or "").strip()
            detail = err or out
            return False, detail or f"osascript failed (rc={result.returncode})"
        except subprocess.TimeoutExpired:
            return False, "osascript timed out"
        except Exception as e:
            return False, f"osascript error: {e}"

    if backend.name == "sendinput":
        # Best-effort: send virtual-key events for common keys.
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)

            if window_id:
                _sendinput_focus_window(window_id)

            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002

            VK_CONTROL = 0x11
            VK_SHIFT = 0x10
            VK_MENU = 0x12  # Alt
            VK_LWIN = 0x5B

            vk_map: dict[str, int] = {
                "Up": 0x26,
                "Down": 0x28,
                "Left": 0x25,
                "Right": 0x27,
                "Return": 0x0D,
                "Tab": 0x09,
                "Escape": 0x1B,
                "BackSpace": 0x08,
                "Delete": 0x2E,
                "Home": 0x24,
                "End": 0x23,
                "Prior": 0x21,
                "Next": 0x22,
                "space": 0x20,
            }

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR),
                ]

            class _INPUT_UNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

            user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            user32.SendInput.restype = wintypes.UINT

            def _vk_input(vk: int, *, keyup: bool) -> INPUT:
                flags = KEYEVENTF_KEYUP if keyup else 0
                return INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUT_UNION(
                        ki=KEYBDINPUT(
                            wVk=wintypes.WORD(int(vk)),
                            wScan=0,
                            dwFlags=flags,
                            time=0,
                            dwExtraInfo=0,
                        )
                    ),
                )

            def _send(inputs: list[INPUT]) -> None:
                if not inputs:
                    return
                arr = (INPUT * len(inputs))(*inputs)
                sent = int(user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT)))
                if sent != len(arr):
                    err = ctypes.get_last_error()
                    raise RuntimeError(
                        f"SendInput sent {sent}/{len(arr)} events (GetLastError={err})"
                    )

            def _vk_for_key(key: str) -> int | None:
                keysym = _map_key(key)
                if keysym in vk_map:
                    return int(vk_map[keysym])
                if keysym.startswith("F") and keysym[1:].isdigit():
                    n = int(keysym[1:])
                    if 1 <= n <= 24:
                        return int(0x70 + (n - 1))
                if len(key) == 1 and key.isalnum():
                    return int(ord(key.upper()))
                return None

            mod_vk_map = {
                "ctrl": VK_CONTROL,
                "control": VK_CONTROL,
                "shift": VK_SHIFT,
                "alt": VK_MENU,
                "meta": VK_LWIN,
                "super": VK_LWIN,
                "cmd": VK_LWIN,
            }

            events: list[INPUT] = []
            for key, mods in resolved:
                mod_vks = [mod_vk_map.get(m) for m in mods if m in mod_vk_map]
                mod_vks = [vk for vk in mod_vks if vk is not None]
                vk = _vk_for_key(key)
                if vk is None:
                    continue
                for mvk in mod_vks:
                    events.append(_vk_input(mvk, keyup=False))
                events.append(_vk_input(vk, keyup=False))
                events.append(_vk_input(vk, keyup=True))
                for mvk in reversed(mod_vks):
                    events.append(_vk_input(mvk, keyup=True))
            _send(events)
            return True, None
        except Exception as e:
            return False, f"sendinput error: {e}"

    return False, backend.error or "No typing backend available"


def perform_type_sequence(
    sequence: list[dict[str, object]],
    *,
    window_id: Optional[str] = None,
    backend: TypingBackend | None = None,
) -> tuple[bool, Optional[str]]:
    """Perform a mixed typing sequence of text + keypress chords.

    This is intended for Zwingli's `type` verb metadata.
    """
    backend = _resolve_typing_backend_cached() if backend is None else backend
    if backend.name in ("none", "unavailable"):
        return False, backend.error or "Typing unavailable"

    pending_keys: list[dict[str, object]] = []

    def _flush_keys() -> tuple[bool, Optional[str]]:
        nonlocal pending_keys
        if not pending_keys:
            return True, None
        ok, err = press_keys(pending_keys, window_id=window_id, backend=backend)
        pending_keys = []
        return ok, err

    for raw in sequence or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        if kind == "key":
            pending_keys.append(raw)
            continue
        if kind == "text":
            ok, err = _flush_keys()
            if not ok:
                return False, err
            text = str(raw.get("text") or "")
            if text:
                ok2, err2 = type_text(text, window_id=window_id, backend=backend)
                if not ok2:
                    return False, err2
            continue

    ok, err = _flush_keys()
    if not ok:
        return False, err
    return True, None
