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

