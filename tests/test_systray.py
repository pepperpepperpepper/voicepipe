from __future__ import annotations

import builtins

import voicepipe.systray as systray_mod
from voicepipe.systray import SystrayManager, get_systray


def test_get_systray_is_safe_without_display(monkeypatch) -> None:
    systray_mod._systray = None
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    systray = get_systray()
    assert systray.available is False


def test_systray_import_exception_is_caught(monkeypatch) -> None:
    systray_mod._systray = None
    # Force the code path that attempts to import pystray, then simulate a
    # runtime failure at import time (e.g. Xlib DisplayNameError).
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pystray":
            raise Exception('Bad display name ""')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    manager = SystrayManager()
    assert manager.available is False
