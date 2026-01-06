"""Subprocess entrypoint used for non-daemon recording mode.

This exists so recording can run as a separate process that can be stopped via
signals or a cross-platform control file while keeping the top-level Click CLI
responsive.
"""

from __future__ import annotations


def run_recording_subprocess() -> None:
    import os
    import signal
    import sys
    import threading
    import time
    from pathlib import Path

    from voicepipe.session import RecordingSession

    recorder = None
    session = None
    timeout_timer = None
    try:
        def _safe_stderr(message: str) -> None:
            err = getattr(sys, "stderr", None)
            if err is None:
                return
            try:
                err.write(message + "\n")
                err.flush()
            except Exception:
                pass

        # Create the session file first so callers can observe that the
        # subprocess is alive even if importing audio dependencies is slow
        # (Windows AV / first-run import jitter).
        session = RecordingSession.create_session()
        audio_file = str(session.get("audio_file") or "")
        control_path = str(session.get("control_path") or "")
        control_file = Path(control_path) if control_path else None

        stop_event = threading.Event()
        requested_action: dict[str, str] = {"action": ""}

        def _cleanup_session() -> None:
            try:
                RecordingSession.cleanup_session(session)
            except Exception:
                pass

        def _request(action: str) -> None:
            if not requested_action["action"]:
                requested_action["action"] = action
            stop_event.set()

        def stop_handler(_signum, _frame) -> None:
            _request("stop")

        def cancel_handler(_signum, _frame) -> None:
            _request("cancel")

        # Signals are best-effort (mostly for Unix), but the cross-platform
        # contract is the control file stored in the session JSON.
        try:
            signal.signal(signal.SIGTERM, stop_handler)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGINT, cancel_handler)
        except Exception:
            pass
        try:
            signal.signal(signal.SIGUSR1, cancel_handler)
        except Exception:
            pass

        from voicepipe.audio import resolve_audio_input
        from voicepipe.config import get_audio_channels, get_audio_sample_rate
        from voicepipe.recorder import AudioRecorder

        resolution = resolve_audio_input(
            preferred_samplerate=get_audio_sample_rate(),
            preferred_channels=get_audio_channels(),
        )
        selection = resolution.selection
        recorder = AudioRecorder(
            device_index=selection.device_index,
            sample_rate=selection.samplerate,
            channels=selection.channels,
            max_duration=None,
        )

        _safe_stderr(
            f"Audio input ({resolution.source}): device={selection.device_index} "
            f"samplerate={selection.samplerate} channels={selection.channels}"
        )
        _safe_stderr(f"Recording started (PID: {os.getpid()})...")
        recorder.start_recording(output_file=audio_file)

        def _timeout_stop() -> None:
            _request("stop")

        timeout_timer = threading.Timer(300, _timeout_stop)
        timeout_timer.daemon = True
        timeout_timer.start()

        last_mtime_ns: int | None = None
        poll_s = 0.05
        while not stop_event.is_set():
            stop_event.wait(timeout=poll_s)
            if stop_event.is_set():
                break

            if control_file is None:
                continue

            try:
                st = control_file.stat()
            except FileNotFoundError:
                continue
            except Exception:
                continue

            mtime_ns = getattr(st, "st_mtime_ns", None)
            if mtime_ns is None:
                try:
                    mtime_ns = int(float(st.st_mtime) * 1_000_000_000)
                except Exception:
                    mtime_ns = None

            if last_mtime_ns is not None and mtime_ns is not None and mtime_ns == last_mtime_ns:
                continue
            last_mtime_ns = mtime_ns

            try:
                cmd = control_file.read_text(encoding="utf-8", errors="replace").strip().lower()
            except Exception:
                continue
            if cmd == "stop":
                _request("stop")
            elif cmd == "cancel":
                _request("cancel")

        action = requested_action["action"] or "stop"
        if timeout_timer:
            try:
                timeout_timer.cancel()
            except Exception:
                pass
            timeout_timer = None

        if action == "cancel":
            if recorder and recorder.recording:
                try:
                    recorder.stop_recording()
                except Exception:
                    pass
            if recorder:
                recorder.cleanup()
            try:
                if audio_file and os.path.exists(audio_file):
                    os.unlink(audio_file)
            except Exception:
                pass
            _cleanup_session()
            raise SystemExit(0)

        # Default: stop and save audio.
        if recorder and recorder.recording:
            try:
                audio_data = recorder.stop_recording()
                if audio_data:
                    recorder.save_to_file(audio_data, audio_file)
            except Exception as e:
                _safe_stderr(f"Error saving audio: {e}")
        if recorder:
            recorder.cleanup()

        # Give the filesystem a brief moment to flush (especially on Windows AV / network drives).
        try:
            if audio_file:
                for _ in range(2):
                    if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                        break
                    time.sleep(0.05)
        except Exception:
            pass

        _cleanup_session()
        raise SystemExit(0)

    except SystemExit:
        raise
    except Exception as e:
        try:
            _safe_stderr(f"Error: {e}")
        except Exception:
            pass
        if recorder:
            recorder.cleanup()
        if session:
            try:
                RecordingSession.cleanup_session(session)
            except Exception:
                pass
        try:
            audio_file = str(session.get("audio_file") or "") if session else ""
            if audio_file and os.path.exists(audio_file):
                os.unlink(audio_file)
        except Exception:
            pass
        raise SystemExit(1)
