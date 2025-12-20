from __future__ import annotations

import importlib
import stat
from pathlib import Path


def _reload_session():
    import voicepipe.session as session

    return importlib.reload(session)


def test_create_and_cleanup_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    session = _reload_session()

    s = session.RecordingSession.create_session()
    audio_file = Path(s["audio_file"])
    assert audio_file.exists()

    state_file = session.RecordingSession.get_state_file(int(s["pid"]))
    assert state_file.exists()

    # Permissions are best-effort; tmpfs should support.
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_file.parent.stat().st_mode) == 0o700

    session.RecordingSession.cleanup_session(s)
    assert not state_file.exists()

