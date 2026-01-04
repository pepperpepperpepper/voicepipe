"""Lightweight recording session tracking.

This module intentionally does not import audio backends (sounddevice/ffmpeg)
so that non-recording CLI commands can run even when audio dependencies are
unavailable.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from voicepipe.paths import audio_tmp_dir, session_state_dir
from voicepipe.platform import pid_is_running


class RecordingSession:
    """Manages recording sessions with PID tracking."""

    STATE_PREFIX = "voicepipe-"

    @classmethod
    def state_dir(cls, *, create: bool = False) -> Path:
        return session_state_dir(create=create)

    @classmethod
    def get_state_file(cls, pid: int | None = None) -> Path:
        """Get the state file path for a given PID."""
        if pid is None:
            pid = os.getpid()
        return cls.state_dir(create=False) / f"{cls.STATE_PREFIX}{pid}.json"

    @classmethod
    def find_active_sessions(cls) -> list[dict[str, Any]]:
        """Find all active recording sessions."""
        state_dir = cls.state_dir(create=False)
        if not state_dir.exists():
            return []

        sessions: list[dict[str, Any]] = []
        for file in state_dir.glob(f"{cls.STATE_PREFIX}*.json"):
            try:
                with open(file, "r", encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                pid = data.get("pid")
                if isinstance(pid, int) and pid_is_running(pid):
                    sessions.append(data)
                else:
                    try:
                        control_path = data.get("control_path")
                        if isinstance(control_path, str) and control_path:
                            Path(control_path).unlink(missing_ok=True)
                    except Exception:
                        pass
                    file.unlink(missing_ok=True)
            except Exception:
                file.unlink(missing_ok=True)
        return sessions

    @classmethod
    def create_session(cls) -> dict[str, Any]:
        """Create a new recording session."""
        active = cls.find_active_sessions()
        if active:
            raise RuntimeError(f"Recording already in progress (PID: {active[0]['pid']})")

        state_dir = cls.state_dir(create=True)
        try:
            os.chmod(state_dir, 0o700)
        except Exception:
            pass

        tmp_dir = audio_tmp_dir(create=True)
        fd, audio_file = tempfile.mkstemp(
            suffix=".wav",
            prefix="voicepipe_",
            dir=str(tmp_dir),
        )
        os.close(fd)

        pid = os.getpid()
        control_path = state_dir / f"{cls.STATE_PREFIX}{pid}.control"
        try:
            control_path.write_text("", encoding="utf-8")
        except Exception:
            pass

        session: dict[str, Any] = {
            "pid": pid,
            "audio_file": audio_file,
            "control_path": str(control_path),
            "recording_id": uuid.uuid4().hex,
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
        try:
            control_path = session.get("control_path")
            if isinstance(control_path, str) and control_path:
                Path(control_path).unlink(missing_ok=True)
        except Exception:
            pass
