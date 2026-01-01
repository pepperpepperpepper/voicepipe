"""Shared helpers for typing text into the active application."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Optional, Tuple

from voicepipe.platform import is_windows


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


TypingBackendName = Literal["wtype", "xdotool", "sendinput", "none", "unavailable"]
SessionType = Literal["wayland", "x11", "windows", "unknown"]


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
      VOICEPIPE_TYPE_BACKEND=auto|wayland|x11|wtype|xdotool|none
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

        # Basic interactive-session check.
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        if not user32.GetForegroundWindow():
            return False, "No interactive desktop session available (no foreground window)"

        if window_id:
            _sendinput_focus_window(window_id)

        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002
        KEYEVENTF_UNICODE = 0x0004
        VK_RETURN = 0x0D

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.ULONG_PTR),
            ]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT

        def _send_inputs(inputs: list[INPUT]) -> bool:
            if not inputs:
                return True
            arr = (INPUT * len(inputs))(*inputs)
            sent = int(user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT)))
            return sent == len(arr)

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
                    return False, "SendInput failed (are you in an interactive desktop session?)"

        if not _flush():
            return False, "SendInput failed (are you in an interactive desktop session?)"

        return True, None
    except Exception as e:
        return False, f"sendinput error: {e}"


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
