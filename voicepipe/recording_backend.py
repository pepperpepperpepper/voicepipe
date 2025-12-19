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
from typing import Any, Literal

from voicepipe.ipc import try_send_request
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


@dataclass(frozen=True)
class StopResult:
    mode: BackendMode
    audio_file: str
    session: dict[str, Any] | None = None


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

    def start(self, *, device: int | None) -> StartResult:
        resp = self._call("start", device=device)
        return StartResult(
            mode=self.mode,
            pid=resp.get("pid") if isinstance(resp.get("pid"), int) else None,
            audio_file=resp.get("audio_file") if isinstance(resp.get("audio_file"), str) else None,
        )

    def stop(self) -> StopResult:
        resp = self._call("stop")
        audio_file = resp.get("audio_file")
        if not isinstance(audio_file, str) or not audio_file:
            raise RecordingError("daemon did not return an audio_file")
        return StopResult(mode=self.mode, audio_file=audio_file, session=None)

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

    def start(self, *, device: int | None) -> StartResult:
        active = RecordingSession.find_active_sessions()
        if active:
            raise RecordingError(
                f"Recording already in progress (PID: {active[0].get('pid')})"
            )

        env = os.environ.copy()
        if device is not None:
            env["VOICEPIPE_DEVICE"] = str(device)

        proc = subprocess.Popen(
            [sys.executable, "-m", "voicepipe.cli", "_record"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(0.5)
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else "") if proc.stderr else ""
            raise RecordingError(f"Error starting recording: {stderr}")

        return StartResult(mode=self.mode, pid=proc.pid)

    def stop(self) -> StopResult:
        session = RecordingSession.get_current_session()
        pid = session["pid"]
        audio_file = session["audio_file"]

        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
        except ProcessLookupError:
            pass

        return StopResult(mode=self.mode, audio_file=str(audio_file), session=session)

    def cancel(self) -> CancelResult:
        session = RecordingSession.get_current_session()
        pid = session["pid"]
        audio_file = session.get("audio_file")

        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            pass

        RecordingSession.cleanup_session(session)

        if audio_file and os.path.exists(audio_file):
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
        self._daemon = DaemonRecorderBackend()
        self._subprocess = SubprocessRecorderBackend()

    def start(self, *, device: int | None) -> StartResult:
        try:
            return self._daemon.start(device=device)
        except BackendUnavailable:
            return self._subprocess.start(device=device)

    def stop(self) -> StopResult:
        try:
            return self._daemon.stop()
        except BackendUnavailable:
            return self._subprocess.stop()

    def cancel(self) -> CancelResult:
        try:
            return self._daemon.cancel()
        except BackendUnavailable:
            return self._subprocess.cancel()

    def status(self) -> StatusResult:
        try:
            return self._daemon.status()
        except BackendUnavailable:
            return self._subprocess.status()

