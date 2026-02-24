from __future__ import annotations

import sys

import pytest

from voicepipe import win_hotkey


def test_parse_hotkey_defaults_to_alt_f5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEPIPE_HOTKEY", raising=False)
    modifiers, vk = win_hotkey._parse_hotkey()
    assert modifiers == win_hotkey.MOD_ALT
    assert vk == win_hotkey.VK_F5


def test_parse_hotkey_accepts_alt_f5(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEPIPE_HOTKEY", "Alt+F5")
    modifiers, vk = win_hotkey._parse_hotkey()
    assert modifiers == win_hotkey.MOD_ALT
    assert vk == win_hotkey.VK_F5


def test_parse_hotkey_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEPIPE_HOTKEY", "Ctrl+F5")
    with pytest.raises(SystemExit):
        win_hotkey._parse_hotkey()


def test_win_hotkey_main_is_windows_only() -> None:
    if sys.platform == "win32":
        pytest.skip("Windows-only behavior")
    with pytest.raises(SystemExit):
        win_hotkey.main()


def test_win_hotkey_main_triggers_toggle_on_wm_hotkey(monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only behavior")

    import ctypes
    from ctypes import wintypes

    calls: list[str] = []

    def fake_run_toggle() -> None:
        calls.append("toggle")

    monkeypatch.setattr(win_hotkey, "_run_toggle", fake_run_toggle)
    monkeypatch.setattr(win_hotkey, "_prewarm_audio", lambda: None)
    monkeypatch.setattr(win_hotkey, "_prewarm_fast", lambda: None)

    class _Thread:
        def __init__(self, *, target, daemon: bool):
            self._target = target
            self.daemon = daemon

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(win_hotkey.threading, "Thread", _Thread)

    class _Func:
        def __init__(self, func):
            self._func = func
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self._func(*args)

    class _User32:
        def __init__(self) -> None:
            self._getmessage_calls = 0

            self.RegisterHotKey = _Func(lambda _hwnd, _id, _mod, _vk: wintypes.BOOL(1))
            self.UnregisterHotKey = _Func(lambda _hwnd, _id: wintypes.BOOL(1))
            self.GetAsyncKeyState = _Func(lambda _vk: wintypes.SHORT(0))
            self.SetWindowsHookExW = _Func(lambda *_a: wintypes.HANDLE(1))
            self.UnhookWindowsHookEx = _Func(lambda _hook: wintypes.BOOL(1))
            self.CallNextHookEx = _Func(lambda *_a: 0)

            def _get_message(msg_ptr, _hwnd, _min, _max):
                self._getmessage_calls += 1
                if self._getmessage_calls == 1:
                    msg = ctypes.cast(msg_ptr, ctypes.POINTER(wintypes.MSG)).contents
                    msg.message = win_hotkey.WM_HOTKEY
                    msg.wParam = win_hotkey._hotkey_id()
                    return 1
                return 0

            self.GetMessageW = _Func(_get_message)

    class _Kernel32:
        def __init__(self) -> None:
            self.GetModuleHandleW = _Func(lambda _name: wintypes.HANDLE(1))

    def _windll(name: str, *_a, **_kw):
        if str(name).lower() == "kernel32":
            return _Kernel32()
        return _User32()

    monkeypatch.setattr(ctypes, "WinDLL", _windll)

    win_hotkey.main()
    assert calls == ["toggle"]
