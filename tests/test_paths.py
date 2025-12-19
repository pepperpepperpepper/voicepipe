from __future__ import annotations

from pathlib import Path

import voicepipe.paths as paths


def test_runtime_dir_prefers_xdg_runtime_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert paths.runtime_dir() == tmp_path


def test_runtime_app_dir_under_xdg_runtime_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert paths.runtime_app_dir() == tmp_path / "voicepipe"


def test_daemon_socket_path_creates_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock = paths.daemon_socket_path(create_dir=True)
    assert sock.name == "voicepipe.sock"
    assert sock.parent == tmp_path / "voicepipe"
    assert sock.parent.exists()


def test_state_dirs_use_xdg_state_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert paths.state_dir() == tmp_path / "voicepipe"
    assert paths.preserved_audio_dir() == tmp_path / "voicepipe" / "audio"
    assert paths.doctor_artifacts_dir() == tmp_path / "voicepipe" / "doctor"


def test_preserved_audio_dir_creates_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out = paths.preserved_audio_dir(create=True)
    assert out.exists()


def test_doctor_artifacts_dir_creates_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    out = paths.doctor_artifacts_dir(create=True)
    assert out.exists()
