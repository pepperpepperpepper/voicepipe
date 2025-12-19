"""Lightweight recording session tracking.

This module intentionally does not import audio backends (sounddevice/ffmpeg)
so that non-recording CLI commands can run even when audio dependencies are
unavailable.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from voicepipe.paths import audio_tmp_dir, session_state_dir


class RecordingSession:
    """Manages recording sessions with PID tracking."""

    STATE_DIR = session_state_dir()
    STATE_PREFIX = "voicepipe-"

    @classmethod
    def get_state_file(cls, pid: int | None = None) -> Path:
        """Get the state file path for a given PID."""
        if pid is None:
            pid = os.getpid()
        return cls.STATE_DIR / f"{cls.STATE_PREFIX}{pid}.json"

    @classmethod
    def find_active_sessions(cls) -> list[dict[str, Any]]:
        """Find all active recording sessions."""
        sessions: list[dict[str, Any]] = []
        for file in cls.STATE_DIR.glob(f"{cls.STATE_PREFIX}*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                pid = data.get("pid")
                if isinstance(pid, int) and cls._is_process_running(pid):
                    sessions.append(data)
                else:
                    file.unlink(missing_ok=True)
            except Exception:
                file.unlink(missing_ok=True)
        return sessions

    @classmethod
    def _is_process_running(cls, pid: int) -> bool:
        """Check if a process with given PID is running."""
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    @classmethod
    def create_session(cls) -> dict[str, Any]:
        """Create a new recording session."""
        active = cls.find_active_sessions()
        if active:
            raise RuntimeError(f"Recording already in progress (PID: {active[0]['pid']})")

        cls.STATE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(cls.STATE_DIR, 0o700)
        except Exception:
            pass

        tmp_dir = audio_tmp_dir(create=True)
        fd, audio_file = tempfile.mkstemp(
            suffix=".wav",
            prefix="voicepipe_",
            dir=str(tmp_dir),
        )
        os.close(fd)

        session: dict[str, Any] = {
            "pid": os.getpid(),
            "audio_file": audio_file,
            "started_at": datetime.now().isoformat(),
        }

        state_file = cls.get_state_file()
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(session, f)
        try:
            os.chmod(state_file, 0o600)
        except Exception:
            pass

        return session

    @classmethod
    def get_current_session(cls) -> dict[str, Any]:
        """Get the current active session."""
        sessions = cls.find_active_sessions()
        if not sessions:
            raise RuntimeError("No active recording session found")
        return sessions[0]

    @classmethod
    def cleanup_session(cls, session: dict[str, Any]) -> None:
        """Clean up a recording session."""
        pid = session.get("pid")
        if isinstance(pid, int):
            state_file = cls.get_state_file(pid)
            state_file.unlink(missing_ok=True)
