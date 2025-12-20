from __future__ import annotations

import subprocess

import pytest

from voicepipe.typing import get_active_window_id, type_text


def test_type_text_accepts_empty() -> None:
    ok, err = type_text("")
    assert ok is True
    assert err is None


def test_type_text_prefers_xdotool_on_x11(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":0")

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
    def fake_which(name: str):
        return "/bin/xdotool" if name == "xdotool" else None

    def fake_run(cmd, **_kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="456\n", stderr="")

    monkeypatch.setattr("voicepipe.typing.shutil.which", fake_which)
    monkeypatch.setattr("voicepipe.typing.subprocess.run", fake_run)
    assert get_active_window_id() == "456"


def test_type_text_uses_wtype_on_wayland(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")

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


def test_type_text_returns_helpful_error_when_no_backend(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr("voicepipe.typing.shutil.which", lambda _name: None)
    ok, err = type_text("hello")
    assert ok is False
    assert err
    assert "No typing backend available" in err

