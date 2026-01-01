"""Fast voicepipe control with optimal transcription handling.

This is intended for use from window-manager hotkeys where startup latency and
stderr visibility matter.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from voicepipe.config import get_transcribe_model, load_environment
from voicepipe.locks import LockHeld, PidFileLock
from voicepipe.paths import logs_dir, preserved_audio_dir, runtime_app_dir
from voicepipe.platform import is_linux, is_windows
from voicepipe.recording_backend import AutoRecorderBackend, RecordingError


DEBOUNCE_MS = 500  # milliseconds

_LOG_FH = None


def _runtime_dir(*, create: bool) -> Path:
    return runtime_app_dir(create=create)


def _debounce_path(*, create_dir: bool) -> Path:
    return _runtime_dir(create=create_dir) / "voicepipe-fast.time"


def _lock_path(*, create_dir: bool) -> Path:
    return _runtime_dir(create=create_dir) / "voicepipe-fast.lock"


def _fast_log_path(*, create_dir: bool) -> Path:
    override = (os.environ.get("VOICEPIPE_FAST_LOG_FILE") or "").strip() or (
        os.environ.get("VOICEPIPE_LOG_FILE") or ""
    ).strip()
    if override:
        try:
            return Path(override).expanduser()
        except Exception:
            return Path(override)

    if is_windows():
        return logs_dir(create=create_dir) / "voicepipe-fast.log"

    # Keep the Unix default in the runtime dir to avoid unexpected I/O on hotkey invocations.
    return _runtime_dir(create=create_dir) / "voicepipe-fast.log"


def _open_log() -> Optional[object]:
    global _LOG_FH
    if _LOG_FH is not None:
        return _LOG_FH
    try:
        path = _fast_log_path(create_dir=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        _LOG_FH = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        _LOG_FH = False  # sentinel: failed
    return None if _LOG_FH is False else _LOG_FH


def fast_log(message: str) -> None:
    line = message if message.endswith("\n") else message + "\n"
    fh = _open_log()
    if fh is not None:
        try:
            fh.write(line)  # type: ignore[attr-defined]
        except Exception:
            pass

    err = getattr(sys, "stderr", None)
    if err is not None:
        try:
            err.write(line)
            err.flush()
        except Exception:
            pass


def _eprint(message: str) -> None:
    err = getattr(sys, "stderr", None)
    if err is None:
        fast_log(message)
        return
    try:
        print(message, file=err)
    except Exception:
        fast_log(message)


def _oprint(message: str) -> None:
    out = getattr(sys, "stdout", None)
    if out is None:
        fast_log(message)
        return
    try:
        print(message, file=out)
    except Exception:
        fast_log(message)


def _parent_is_fluxbox() -> bool:
    if not is_linux():
        return False
    try:
        ppid = os.getppid()
        comm_path = Path("/proc") / str(ppid) / "comm"
        if not comm_path.exists():
            return False
        return comm_path.read_text(encoding="utf-8").strip() == "fluxbox"
    except Exception:
        return False


def send_transcribe_request(audio_file: str) -> str:
    """Transcribe audio using daemon when available."""
    # Keep imports out of the `start` hot path.
    from voicepipe.transcription import transcribe_audio_file

    try:
        model = get_transcribe_model()
        return transcribe_audio_file(
            audio_file,
            model=model,
            prefer_daemon=True,
        )
    except Exception as e:
        fast_log(f"[TRANSCRIBE] Error: {e}")
        return ""


def check_debounce() -> bool:
    """Check if enough time has passed since last invocation."""
    try:
        current_time = int(time.time() * 1000)  # milliseconds

        # Try to read last time
        path = _debounce_path(create_dir=True)
        if path.exists():
            last_time = int(path.read_text(encoding="utf-8").strip())

            if current_time - last_time < DEBOUNCE_MS:
                fast_log(
                    f"[DEBOUNCE] Skipping - last: {last_time}, current: {current_time}, "
                    f"diff: {current_time - last_time}ms",
                )
                return False  # Too soon, ignore

        # Write current time
        path.write_text(str(current_time), encoding="utf-8")

        fast_log(f"[DEBOUNCE] Allowing - current: {current_time}")
        return True
    except Exception as e:
        fast_log(f"[DEBOUNCE] Error: {e}, allowing action")
        return True


def execute_toggle() -> None:
    """Execute toggle command logic."""
    try:
        fast_log("[TOGGLE] Starting toggle execution")

        backend = AutoRecorderBackend()
        status = backend.status()
        fast_log(f"[TOGGLE] Status: {status.mode}:{status.status}")

        if status.status == "recording":
            # Keep imports out of the `start` hot path.
            from voicepipe.typing import (
                get_active_window_id,
                resolve_typing_backend,
                type_text,
            )

            typing_backend = resolve_typing_backend()
            # Capture the current active window early so we can type back into it
            # after transcription (hotkey invocations sometimes lose focus).
            target_window = None
            if typing_backend.supports_window_id:
                target_window = get_active_window_id()
                if target_window:
                    fast_log(f"[TOGGLE] Target window: {target_window}")
                else:
                    fast_log("[TOGGLE] Target window: (unknown)")

            fast_log("[TOGGLE] Recording active, stopping...")
            # Stop and transcribe
            stop_result = backend.stop()
            audio_file = stop_result.audio_file
            fast_log(f"[TOGGLE] Audio file: {audio_file}")

            text = send_transcribe_request(audio_file)
            transcription_ok = False
            if text:
                cleaned_text = text.rstrip()
                fast_log(f"[TOGGLE] Transcription: {cleaned_text}")
                # Always persist the last transcript for debugging/recovery.
                try:
                    (_runtime_dir(create=True) / "voicepipe-last.txt").write_text(
                        cleaned_text + "\n", encoding="utf-8"
                    )
                except Exception:
                    pass
                typed_ok, type_err = type_text(
                    cleaned_text,
                    window_id=target_window,
                    backend=typing_backend,
                )
                if not typed_ok:
                    fast_log(f"[TOGGLE] Warning: typing failed: {type_err}")
                transcription_ok = True
            else:
                fast_log("[TOGGLE] No transcription returned")

            # Clean up
            if transcription_ok:
                if os.path.exists(audio_file):
                    os.unlink(audio_file)
                    fast_log(f"[TOGGLE] Cleaned up audio file: {audio_file}")
            else:
                try:
                    dst_dir = preserved_audio_dir(create=True)
                    dst = dst_dir / Path(audio_file).name
                    shutil.move(audio_file, dst)
                    audio_file = str(dst)
                except Exception:
                    pass
                fast_log(f"[TOGGLE] Preserved audio file: {audio_file}")
        else:
            fast_log("[TOGGLE] Starting recording...")
            # Start recording
            backend.start(device=None)
    except RecordingError as e:
        fast_log(f"[TOGGLE] Recording error: {e}")
        raise SystemExit(1)
    except Exception as e:
        fast_log(f"[TOGGLE] Unexpected error: {e}")
        raise


def main(argv: Optional[list[str]] = None) -> None:
    # Ensure env-file config is visible (hotkey workflows don't load shell rc
    # files). Avoid loading a random local `.env` from whatever cwd the WM uses.
    load_environment(load_cwd_dotenv=False)

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 1:
        _eprint("Usage: voicepipe-fast [start|stop|toggle]")
        raise SystemExit(1)

    if args[0] in ("-h", "--help", "help"):
        _oprint("Usage: voicepipe-fast [start|stop|toggle]")
        raise SystemExit(0)

    cmd = args[0]
    if cmd not in ("start", "stop", "toggle"):
        _eprint(f"Error: unknown command: {cmd}")
        _eprint("Usage: voicepipe-fast [start|stop|toggle]")
        raise SystemExit(2)

    # For toggle command, use file locking to prevent concurrent execution
    if cmd == "toggle":
        try:
            fast_log("[MAIN] Toggle command received")
            with PidFileLock(_lock_path(create_dir=True)):
                fast_log("[MAIN] Lock acquired")
                # Also check debounce inside the lock to prevent rapid toggles
                if not check_debounce():
                    fast_log("[MAIN] Debounced, exiting")
                    raise SystemExit(0)  # Exit silently if debounced
                fast_log("[MAIN] Executing toggle")
                execute_toggle()
                fast_log("[MAIN] Toggle completed")
                return
        except LockHeld:
            fast_log("[MAIN] Lock already held, exiting")
            # Another instance is running, exit silently
            raise SystemExit(0)

    try:
        backend = AutoRecorderBackend()
        if cmd == "start":
            status = backend.status()
            if status.status == "recording":
                raise SystemExit(0)  # Already recording, exit silently

            backend.start(device=None)
            return

        elif cmd == "stop":
            status = backend.status()
            if status.status != "recording":
                raise SystemExit(0)  # Not recording, exit silently

            stop_result = backend.stop()
            audio_file = stop_result.audio_file
            if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                text = send_transcribe_request(audio_file)
                # Output text
                if text:
                    out = getattr(sys, "stdout", None)
                    if out is not None:
                        print(text, file=out)
                    else:
                        fast_log(text)
                # Clean up
                if text and os.path.exists(audio_file):
                    os.unlink(audio_file)
                elif not text:
                    try:
                        dst_dir = preserved_audio_dir(create=True)
                        dst = dst_dir / Path(audio_file).name
                        shutil.move(audio_file, dst)
                        audio_file = str(dst)
                    except Exception:
                        pass
                    fast_log(f"[STOP] Preserved audio file: {audio_file}")
            return

    except RecordingError as e:
        _eprint(f"Error: {e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        _eprint(f"Error: {e}")
        raise SystemExit(1)


def toggle_main(argv: Optional[list[str]] = None) -> None:
    """Entry point for a dedicated one-key hotkey command.

    This mirrors: `voicepipe-fast toggle`
    """
    del argv
    main(["toggle"])


if __name__ == "__main__":
    main()
