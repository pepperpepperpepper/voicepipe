from __future__ import annotations

import builtins

import voicepipe.systray as systray_mod
from voicepipe.systray import SystrayManager, get_systray


def test_get_systray_is_safe_without_display(monkeypatch) -> None:
    systray_mod._systray = None
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    # Ensure we don't hydrate DISPLAY from systemd or filesystem heuristics in
    # this unit test; we want the pure "no display" behavior.
    monkeypatch.setattr(systray_mod, "_load_gui_env_from_systemd", lambda *a, **k: None)
    monkeypatch.setattr(systray_mod, "_infer_display_from_x11_socket", lambda: None)
    monkeypatch.setattr(systray_mod, "_infer_wayland_display", lambda: None)
    systray = get_systray()
    assert systray.available is False


def test_systray_import_exception_is_caught(monkeypatch) -> None:
    systray_mod._systray = None
    # Force the code path that attempts to import pystray, then simulate a
    # runtime failure at import time (e.g. Xlib DisplayNameError).
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(systray_mod, "_can_use_sni", lambda: False)

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pystray":
            raise Exception('Bad display name ""')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    manager = SystrayManager()
    assert manager.available is False


def test_systray_can_be_available_without_pystray_when_sni_is_enabled(monkeypatch) -> None:
    systray_mod._systray = None
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(systray_mod, "_can_use_sni", lambda: True)
    manager = SystrayManager()
    assert manager.available is True
