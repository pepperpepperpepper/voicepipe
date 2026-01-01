from __future__ import annotations

import subprocess
import sys
import time

import pytest

from voicepipe.typing import get_active_window_id, resolve_typing_backend, type_text


def test_type_text_accepts_empty() -> None:
    ok, err = type_text("")
    assert ok is True
    assert err is None


def test_type_text_prefers_xdotool_on_x11(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)

    calls: list[list[str]] = []

    def fake_which(name: str):
        if name == "xdotool":
            return "/bin/xdotool"
        return None

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    monkeypatch.setattr("voicepipe.typing.subprocess.run", fake_run)

    ok, err = type_text("hello", window_id="123")
    assert ok is True
    assert err is None
    assert calls
    assert calls[0][:3] == ["/bin/xdotool", "type", "--clearmodifiers"]
    assert "--window" in calls[0]


def test_get_active_window_id_uses_xdotool(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    def fake_which(name: str):
        return "/bin/xdotool" if name == "xdotool" else None

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="456\n", stderr="")

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    monkeypatch.setattr("voicepipe.typing.subprocess.run", fake_run)
    assert get_active_window_id() == "456"


def test_type_text_uses_wtype_on_wayland(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)

    def fake_which(name: str):
        if name == "wtype":
            return "/bin/wtype"
        return None

    calls: list[list[str]] = []
    run_count = 0

    def fake_run(cmd, **kwargs):
        nonlocal run_count
        run_count += 1
        calls.append(list(cmd))
        # First attempt fails, second attempt (stdin mode) succeeds.
        if run_count == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="nope")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    monkeypatch.setattr("voicepipe.typing.subprocess.run", fake_run)

    ok, err = type_text("hello")
    assert ok is True
    assert err is None
    assert calls[0] == ["/bin/wtype", "hello"]
    assert calls[1] == ["/bin/wtype"]


def test_resolve_typing_backend_prefers_wtype_when_both_display_and_wayland(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)

    def fake_which(name: str):
        if name == "wtype":
            return "/bin/wtype"
        if name == "xdotool":
            return "/bin/xdotool"
        return None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)

    backend = resolve_typing_backend()
    assert backend.name == "wtype"
    assert backend.supports_window_id is False


def test_resolve_typing_backend_override_none(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "none")
    backend = resolve_typing_backend()
    assert backend.name == "none"
    assert backend.error


def test_resolve_typing_backend_override_wtype(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "wtype")

    def fake_which(name: str):
        return "/bin/wtype" if name == "wtype" else None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)

    backend = resolve_typing_backend()
    assert backend.name == "wtype"


def test_resolve_typing_backend_override_xdotool(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "xdotool")

    def fake_which(name: str):
        return "/bin/xdotool" if name == "xdotool" else None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)

    backend = resolve_typing_backend()
    assert backend.name == "xdotool"
    assert backend.supports_window_id is True


def test_resolve_typing_backend_alias_wayland(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "wayland")

    def fake_which(name: str):
        return "/bin/wtype" if name == "wtype" else None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)

    backend = resolve_typing_backend()
    assert backend.name == "wtype"


def test_resolve_typing_backend_alias_x11(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "x11")

    def fake_which(name: str):
        return "/bin/xdotool" if name == "xdotool" else None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)

    backend = resolve_typing_backend()
    assert backend.name == "xdotool"
    assert backend.supports_window_id is True


def test_type_text_returns_helpful_error_when_no_backend(monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("Linux-only typing backend")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)
    monkeypatch.setattr("voicepipe.typing.shutil.which", lambda _name: None)
    ok, err = type_text("hello")
    assert ok is False
    assert err
    assert "No display session detected" in err


def test_resolve_typing_backend_auto_is_sendinput_on_windows(monkeypatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only behavior")
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)
    backend = resolve_typing_backend()
    assert backend.name == "sendinput"
    assert backend.supports_window_id is True


def test_resolve_typing_backend_override_sendinput_on_windows(monkeypatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only behavior")
    monkeypatch.setenv("VOICEPIPE_TYPE_BACKEND", "sendinput")
    backend = resolve_typing_backend()
    assert backend.name == "sendinput"


def test_resolve_typing_backend_auto_is_osascript_on_macos(monkeypatch) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")
    monkeypatch.delenv("VOICEPIPE_TYPE_BACKEND", raising=False)

    def fake_which(name: str):
        return "/usr/bin/osascript" if name == "osascript" else None

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    backend = resolve_typing_backend()
    assert backend.name == "osascript"
    assert backend.session_type == "macos"


def test_type_text_uses_osascript_backend(monkeypatch) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    def fake_which(name: str):
        return "/usr/bin/osascript" if name == "osascript" else None

    calls: list[list[str]] = []
    argv: list[str] = []

    def fake_run(cmd, **kwargs):
        nonlocal argv
        calls.append(list(cmd))
        argv = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    monkeypatch.setattr("voicepipe.typing.subprocess.run", fake_run)

    backend = resolve_typing_backend()
    ok, err = type_text("hello\nworld", backend=backend)
    assert ok is True
    assert err is None
    assert calls
    assert argv[:2] == ["/usr/bin/osascript", "-"]
    assert argv[2] == "hello\rworld"


@pytest.mark.desktop
@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop-only test")
def test_sendinput_types_into_tkinter_window() -> None:
    tkinter = pytest.importorskip("tkinter")

    # Avoid importing ctypes on non-Windows platforms.
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    SW_RESTORE = 9

    root = tkinter.Tk()
    try:
        root.title("voicepipe desktop typing test")
        root.geometry("520x160+40+40")
        text_widget = tkinter.Text(root, width=60, height=4)
        text_widget.pack(fill="both", expand=True)

        root.update()
        hwnd = int(root.winfo_id())

        # Best-effort focus/foreground loop; if this fails, the runner is likely
        # locked or not running in an interactive desktop session.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                root.deiconify()
                root.lift()
                root.attributes("-topmost", True)
                root.attributes("-topmost", False)
                text_widget.focus_force()
                root.update()
            except Exception:
                pass

            try:
                user32.ShowWindow(wintypes.HWND(hwnd), SW_RESTORE)
                user32.SetForegroundWindow(wintypes.HWND(hwnd))
            except Exception:
                pass

            try:
                fg = int(user32.GetForegroundWindow() or 0)
            except Exception:
                fg = 0
            if fg == hwnd:
                break
            time.sleep(0.05)
            try:
                root.update()
            except Exception:
                pass
        else:
            pytest.skip("Unable to focus the test window (runner must be unlocked + interactive)")

        expected = "hello\nworld ✅"
        ok, err = type_text(expected, window_id=str(hwnd))
        if not ok and err and "No interactive desktop session" in err:
            pytest.skip(err)

        assert ok is True, err

        # Let Tk process any queued key events.
        for _ in range(40):
            root.update()
            time.sleep(0.02)

        got = text_widget.get("1.0", "end-1c").replace("\r\n", "\n").replace("\r", "\n")
        assert got == expected
    finally:
        try:
            root.destroy()
        except Exception:
            pass
