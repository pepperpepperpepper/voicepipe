"""Recording control abstractions used by the CLI.

The goal is to keep Click commands thin and make the "daemon vs subprocess"
fallback behavior consistent.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from voicepipe.ipc import try_send_request
from voicepipe.config import get_daemon_mode
from voicepipe.platform import is_windows, pid_is_running
from voicepipe.session import RecordingSession


BackendMode = Literal["daemon", "subprocess"]


class RecordingError(RuntimeError):
    pass


class BackendUnavailable(RecordingError):
    """Signals the caller should try a different backend."""


@dataclass(frozen=True)
class StartResult:
    mode: BackendMode
    pid: int | None = None
    audio_file: str | None = None
    recording_id: str | None = None


@dataclass(frozen=True)
class StopResult:
    mode: BackendMode
    audio_file: str
    session: dict[str, Any] | None = None
    recording_id: str | None = None


@dataclass(frozen=True)
class StatusResult:
    mode: BackendMode
    status: str
    pid: int | None = None


@dataclass(frozen=True)
class CancelResult:
    mode: BackendMode


class DaemonRecorderBackend:
    mode: BackendMode = "daemon"

    def _call(self, command: str, **kwargs: Any) -> dict[str, Any]:
        resp = try_send_request(command, **kwargs)
        if resp is None:
            raise BackendUnavailable("daemon unavailable")
        if resp.get("error"):
            raise RecordingError(str(resp.get("error")))
        return resp

    def start(self, *, device: str | int | None) -> StartResult:
        resp = self._call("start", device=device)
        return StartResult(
            mode=self.mode,
            pid=resp.get("pid") if isinstance(resp.get("pid"), int) else None,
            audio_file=resp.get("audio_file") if isinstance(resp.get("audio_file"), str) else None,
            recording_id=resp.get("recording_id")
            if isinstance(resp.get("recording_id"), str)
            else None,
        )

    def stop(self) -> StopResult:
        resp = self._call("stop")
        audio_file = resp.get("audio_file")
        if not isinstance(audio_file, str) or not audio_file:
            raise RecordingError("daemon did not return an audio_file")
        recording_id = resp.get("recording_id")
        return StopResult(
            mode=self.mode,
            audio_file=audio_file,
            session=None,
            recording_id=recording_id if isinstance(recording_id, str) else None,
        )

    def cancel(self) -> CancelResult:
        self._call("cancel")
        return CancelResult(mode=self.mode)

    def status(self) -> StatusResult:
        resp = self._call("status")
        status = resp.get("status")
        return StatusResult(
            mode=self.mode,
            status=str(status) if status is not None else "unknown",
            pid=resp.get("pid") if isinstance(resp.get("pid"), int) else None,
        )


class SubprocessRecorderBackend:
    mode: BackendMode = "subprocess"

    def _spawn(self, argv: list[str], *, env: dict[str, str]) -> subprocess.Popen:
        kwargs: dict[str, Any] = {}
        if is_windows():
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            argv,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            **kwargs,
        )

    def _write_control(self, control_path: str, command: str) -> None:
        path = Path(control_path)
        try:
            path.write_text(command + "\n", encoding="utf-8")
        except Exception as e:
            raise RecordingError(f"Failed to write control command ({command}) to {path}: {e}") from e

    def _wait_for_exit(self, pid: int, *, timeout_s: float) -> None:
        deadline = time.monotonic() + float(timeout_s)
        while time.monotonic() < deadline:
            if not pid_is_running(int(pid)):
                return
            time.sleep(0.05)
        raise RecordingError(f"Timed out waiting for recording subprocess to exit (pid={pid})")

    def start(self, *, device: str | int | None) -> StartResult:
        active = RecordingSession.find_active_sessions()
        if active:
            raise RecordingError(
                f"Recording already in progress (PID: {active[0].get('pid')})"
            )

        env = os.environ.copy()
        if device is not None:
            env["VOICEPIPE_DEVICE"] = str(device)

        proc = self._spawn([sys.executable, "-m", "voicepipe.cli", "_record"], env=env)

        state_file = RecordingSession.get_state_file(proc.pid)
        timeout_s = 5.0
        raw_timeout = os.environ.get("VOICEPIPE_RECORDING_INIT_TIMEOUT")
        if raw_timeout:
            try:
                timeout_s = max(1.0, float(raw_timeout))
            except ValueError:
                pass
        deadline = time.monotonic() + timeout_s
        session: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                stderr = (proc.stderr.read() if proc.stderr else "") if proc.stderr else ""
                raise RecordingError(f"Error starting recording: {stderr}")
            try:
                if state_file.exists():
                    session = RecordingSession.get_current_session()
                    control = session.get("control_path") if isinstance(session, dict) else None
                    if isinstance(control, str) and control:
                        break
            except Exception:
                session = None
            time.sleep(0.05)

        if not session:
            raise RecordingError(
                "Timed out waiting for recording subprocess to initialize (no session file)."
            )
        control = session.get("control_path")
        if not isinstance(control, str) or not control:
            raise RecordingError(
                "Timed out waiting for recording subprocess to initialize (missing control_path)."
            )

        recording_id = None
        audio_file = None
        if session:
            rid = session.get("recording_id")
            if isinstance(rid, str) and rid:
                recording_id = rid
            af = session.get("audio_file")
            if isinstance(af, str) and af:
                audio_file = af

        return StartResult(
            mode=self.mode,
            pid=proc.pid,
            audio_file=audio_file,
            recording_id=recording_id,
        )

    def stop(self) -> StopResult:
        session = RecordingSession.get_current_session()
        pid = session.get("pid")
        audio_file = session.get("audio_file")
        recording_id = session.get("recording_id") if isinstance(session, dict) else None

        if not isinstance(pid, int):
            raise RecordingError("Invalid session PID")
        if not isinstance(audio_file, str) or not audio_file:
            raise RecordingError("Invalid session audio_file")
        control_path = session.get("control_path")
        if not isinstance(control_path, str) or not control_path:
            raise RecordingError("Session is missing control_path (upgrade mismatch?)")

        self._write_control(control_path, "stop")
        if pid_is_running(int(pid)):
            self._wait_for_exit(int(pid), timeout_s=10.0)

        # Best-effort: wait for the audio file to exist and have a stable, non-zero size.
        try:
            last_size = -1
            for _ in range(3):
                if not os.path.exists(audio_file):
                    time.sleep(0.05)
                    continue
                size = os.path.getsize(audio_file)
                if size > 0 and size == last_size:
                    break
                last_size = size
                time.sleep(0.05)
        except Exception:
            pass

        return StopResult(
            mode=self.mode,
            audio_file=str(audio_file),
            session=session,
            recording_id=recording_id if isinstance(recording_id, str) else None,
        )

    def cancel(self) -> CancelResult:
        session = RecordingSession.get_current_session()
        pid = session.get("pid")
        audio_file = session.get("audio_file")
        if not isinstance(pid, int):
            raise RecordingError("Invalid session PID")

        control_path = session.get("control_path")
        if not isinstance(control_path, str) or not control_path:
            raise RecordingError("Session is missing control_path (upgrade mismatch?)")

        self._write_control(control_path, "cancel")
        if pid_is_running(int(pid)):
            self._wait_for_exit(int(pid), timeout_s=10.0)

        # Cleanup is best-effort; the subprocess also cleans up its own session.
        try:
            RecordingSession.cleanup_session(session)
        except Exception:
            pass

        if isinstance(audio_file, str) and audio_file and os.path.exists(audio_file):
            try:
                os.unlink(audio_file)
            except Exception:
                pass

        return CancelResult(mode=self.mode)

    def status(self) -> StatusResult:
        try:
            session = RecordingSession.get_current_session()
            pid = session.get("pid")
            return StatusResult(
                mode=self.mode,
                status="recording",
                pid=int(pid) if isinstance(pid, int) else None,
            )
        except RuntimeError:
            return StatusResult(mode=self.mode, status="idle", pid=None)


class AutoRecorderBackend:
    """Prefer daemon, but fall back to a subprocess when unavailable."""

    def __init__(self) -> None:
        self._daemon_mode = get_daemon_mode(load_env=True)
        if is_windows() and self._daemon_mode == "always":
            raise RecordingError(
                "VOICEPIPE_DAEMON_MODE=always is not supported on Windows yet.\n\n"
                "Use `VOICEPIPE_DAEMON_MODE=never` (recommended) and run:\n"
                "  voicepipe start|stop|cancel|status\n"
                "  voicepipe-fast toggle"
            )
        self._daemon = DaemonRecorderBackend()
        self._subprocess = SubprocessRecorderBackend()

    def _daemon_allowed(self) -> bool:
        if self._daemon_mode == "never":
            return False
        if self._daemon_mode == "auto" and is_windows():
            return False
        return True

    def _daemon_required(self) -> bool:
        return self._daemon_mode == "always"

    def _daemon_status(self) -> StatusResult | None:
        if not self._daemon_allowed():
            return None
        try:
            return self._daemon.status()
        except BackendUnavailable:
            return None

    def _subprocess_status(self) -> StatusResult:
        return self._subprocess.status()

    def start(self, *, device: str | int | None) -> StartResult:
        if self._daemon_allowed():
            try:
                return self._daemon.start(device=device)
            except BackendUnavailable:
                if self._daemon_required():
                    raise RecordingError("Daemon mode required but daemon is unavailable")
        return self._subprocess.start(device=device)

    def stop(self) -> StopResult:
        daemon_status = self._daemon_status()
        if daemon_status and daemon_status.status == "recording":
            return self._daemon.stop()

        if self._daemon_required() and daemon_status is None and self._daemon_allowed():
            raise RecordingError("Daemon mode required but daemon is unavailable")

        subprocess_status = self._subprocess_status()
        if subprocess_status.status == "recording":
            return self._subprocess.stop()

        raise RecordingError("No recording in progress")

    def cancel(self) -> CancelResult:
        daemon_status = self._daemon_status()
        if daemon_status and daemon_status.status == "recording":
            return self._daemon.cancel()

        if self._daemon_required() and daemon_status is None and self._daemon_allowed():
            raise RecordingError("Daemon mode required but daemon is unavailable")

        subprocess_status = self._subprocess_status()
        if subprocess_status.status == "recording":
            return self._subprocess.cancel()

        raise RecordingError("No recording in progress")

    def status(self) -> StatusResult:
        daemon_status = self._daemon_status()
        if daemon_status and daemon_status.status == "recording":
            return daemon_status

        if self._daemon_required() and daemon_status is None and self._daemon_allowed():
            raise RecordingError("Daemon mode required but daemon is unavailable")

        subprocess_status = self._subprocess_status()
        if subprocess_status.status == "recording":
            return subprocess_status

        if daemon_status is not None:
            return daemon_status
        return subprocess_status
