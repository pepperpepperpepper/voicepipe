"""Fast voicepipe control with optimal transcription handling.

This is intended for use from window-manager hotkeys where startup latency and
stderr visibility matter.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from voicepipe.ipc import IpcError, IpcUnavailable, daemon_socket_path, send_request
from voicepipe.paths import preserved_audio_dir, runtime_app_dir, transcriber_socket_path
from voicepipe.transcription import transcribe_audio_file
from voicepipe.typing import get_active_window_id, type_text


SOCKET_PATH = daemon_socket_path()

TMP_DIR = runtime_app_dir(create=True)
LOG_FILE = TMP_DIR / "voicepipe-fast.log"

# When invoked from a hotkey (no tty), stderr is usually discarded. Mirror it to
# a log file so failures (e.g. xdotool) are visible.
def _parent_is_fluxbox() -> bool:
    try:
        ppid = os.getppid()
        comm_path = Path("/proc") / str(ppid) / "comm"
        if not comm_path.exists():
            return False
        return comm_path.read_text(encoding="utf-8").strip() == "fluxbox"
    except Exception:
        return False


_LOG_ENABLED = (
    (not sys.stderr.isatty())
    or os.environ.get("VOICEPIPE_FAST_LOG") == "1"
    or _parent_is_fluxbox()
)
if _LOG_ENABLED:
    try:
        _log_fh = open(LOG_FILE, "a", buffering=1)
        os.dup2(_log_fh.fileno(), sys.stderr.fileno())
    except Exception:
        pass

DEBOUNCE_FILE = str(TMP_DIR / "voicepipe-fast.time")
DEBOUNCE_MS = 500  # milliseconds
LOCK_FILE = str(TMP_DIR / "voicepipe-fast.lock")

TRANSCRIBER_SOCKET = transcriber_socket_path()


def send_cmd(cmd: str) -> dict:
    """Send command to daemon via Unix socket."""
    read_timeout = 2.0 if cmd == "status" else 5.0
    return send_request(
        cmd,
        socket_path=SOCKET_PATH,
        connect_timeout=2.0,
        read_timeout=read_timeout,
    )


def send_transcribe_request(audio_file: str) -> str:
    """Transcribe audio using daemon when available."""
    if not TRANSCRIBER_SOCKET.exists():
        print(
            f"[TRANSCRIBE] Transcriber socket not found: {TRANSCRIBER_SOCKET}",
            file=sys.stderr,
        )
        print(
            "[TRANSCRIBE] Start it with: systemctl --user start voicepipe.target",
            file=sys.stderr,
        )
        print("[TRANSCRIBE] Or: voicepipe service start", file=sys.stderr)
    try:
        return transcribe_audio_file(
            audio_file,
            model="gpt-4o-transcribe",
            prefer_daemon=True,
        )
    except Exception as e:
        print(f"[TRANSCRIBE] Error: {e}", file=sys.stderr)
        return ""


class FileLock:
    """Simple file-based lock using fcntl."""

    def __init__(self, lockfile: str):
        self.lockfile = lockfile
        self.fd = None

    def __enter__(self) -> "FileLock":
        self.fd = open(self.lockfile, "w")
        try:
            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except IOError:
            # Lock is held by another process
            self.fd.close()
            self.fd = None
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.fd:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            self.fd.close()


def check_debounce() -> bool:
    """Check if enough time has passed since last invocation."""
    try:
        current_time = int(time.time() * 1000)  # milliseconds

        # Try to read last time
        if os.path.exists(DEBOUNCE_FILE):
            with open(DEBOUNCE_FILE, "r") as f:
                last_time = int(f.read().strip())

            if current_time - last_time < DEBOUNCE_MS:
                print(
                    f"[DEBOUNCE] Skipping - last: {last_time}, current: {current_time}, "
                    f"diff: {current_time - last_time}ms",
                    file=sys.stderr,
                )
                return False  # Too soon, ignore

        # Write current time
        with open(DEBOUNCE_FILE, "w") as f:
            f.write(str(current_time))

        print(f"[DEBOUNCE] Allowing - current: {current_time}", file=sys.stderr)
        return True
    except Exception as e:
        print(f"[DEBOUNCE] Error: {e}, allowing action", file=sys.stderr)
        return True


def execute_toggle() -> None:
    """Execute toggle command logic."""
    try:
        print("[TOGGLE] Starting toggle execution", file=sys.stderr)
        status = send_cmd("status")
        print(f"[TOGGLE] Status: {status}", file=sys.stderr)

        if status.get("status") == "recording":
            # Capture the current active window early so we can type back into it
            # after transcription (hotkey invocations sometimes lose focus).
            target_window = get_active_window_id()
            if target_window:
                print(f"[TOGGLE] Target window: {target_window}", file=sys.stderr)
            else:
                print("[TOGGLE] Target window: (unknown)", file=sys.stderr)

            print("[TOGGLE] Recording active, stopping...", file=sys.stderr)
            # Stop and transcribe
            result = send_cmd("stop")
            print(f"[TOGGLE] Stop result: {result}", file=sys.stderr)

            if "error" not in result and "audio_file" in result:
                audio_file = result["audio_file"]
                print(f"[TOGGLE] Audio file: {audio_file}", file=sys.stderr)

                text = send_transcribe_request(audio_file)
                if text:
                    cleaned_text = text.rstrip()
                    print(f"[TOGGLE] Transcription: {cleaned_text}", file=sys.stderr)
                    # Always persist the last transcript for debugging/recovery.
                    try:
                        (TMP_DIR / "voicepipe-last.txt").write_text(
                            cleaned_text + "\n", encoding="utf-8"
                        )
                    except Exception:
                        pass
                    typed_ok, type_err = type_text(
                        cleaned_text, window_id=target_window
                    )
                    if not typed_ok:
                        print(
                            f"[TOGGLE] Warning: typing failed: {type_err}",
                            file=sys.stderr,
                        )
                    transcription_ok = True
                else:
                    print("[TOGGLE] No transcription returned", file=sys.stderr)
                    transcription_ok = False

                # Clean up
                if transcription_ok:
                    if os.path.exists(audio_file):
                        os.unlink(audio_file)
                        print(
                            f"[TOGGLE] Cleaned up audio file: {audio_file}",
                            file=sys.stderr,
                        )
                else:
                    try:
                        dst_dir = preserved_audio_dir(create=True)
                        dst = dst_dir / Path(audio_file).name
                        shutil.move(audio_file, dst)
                        audio_file = str(dst)
                    except Exception:
                        pass
                    print(f"[TOGGLE] Preserved audio file: {audio_file}", file=sys.stderr)
            else:
                print(
                    f"[TOGGLE] Stop error: {result.get('error', 'Unknown error')}",
                    file=sys.stderr,
                )
        else:
            print("[TOGGLE] Starting recording...", file=sys.stderr)
            # Start recording
            result = send_cmd("start")
            print(f"[TOGGLE] Start result: {result}", file=sys.stderr)
            if "error" in result:
                print(f"[TOGGLE] Start error: {result['error']}", file=sys.stderr)
    except IpcUnavailable as e:
        print(f"[TOGGLE] IPC unavailable: {e}", file=sys.stderr)
        print("Error: Cannot connect to voicepipe daemon. Is it running?", file=sys.stderr)
        print(
            "Start it with: systemctl --user start voicepipe.target",
            file=sys.stderr,
        )
        print("Or: voicepipe service start", file=sys.stderr)
        raise SystemExit(1)
    except IpcError as e:
        print(f"[TOGGLE] IPC error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as e:
        print(f"[TOGGLE] Unexpected error: {e}", file=sys.stderr)
        raise


def main(argv: Optional[list[str]] = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 1:
        print("Usage: voicepipe-fast [start|stop|toggle]")
        raise SystemExit(1)

    cmd = args[0]

    # For toggle command, use file locking to prevent concurrent execution
    if cmd == "toggle":
        try:
            print("[MAIN] Toggle command received", file=sys.stderr)
            with FileLock(LOCK_FILE):
                print("[MAIN] Lock acquired", file=sys.stderr)
                # Also check debounce inside the lock to prevent rapid toggles
                if not check_debounce():
                    print("[MAIN] Debounced, exiting", file=sys.stderr)
                    raise SystemExit(0)  # Exit silently if debounced
                print("[MAIN] Executing toggle", file=sys.stderr)
                execute_toggle()
                print("[MAIN] Toggle completed", file=sys.stderr)
                return
        except IOError:
            print("[MAIN] Lock already held, exiting", file=sys.stderr)
            # Another instance is running, exit silently
            raise SystemExit(0)

    try:
        if cmd == "start":
            status = send_cmd("status")
            if status.get("status") == "recording":
                raise SystemExit(0)  # Already recording, exit silently

            result = send_cmd("start")
            if "error" in result:
                print(f"Error: {result['error']}", file=sys.stderr)
                raise SystemExit(1)

        elif cmd == "stop":
            status = send_cmd("status")
            if status.get("status") != "recording":
                raise SystemExit(0)  # Not recording, exit silently

            result = send_cmd("stop")
            if "error" not in result and "audio_file" in result:
                audio_file = result["audio_file"]
                if os.path.exists(audio_file) and os.path.getsize(audio_file) > 0:
                    text = send_transcribe_request(audio_file)
                    # Output text
                    if text:
                        print(text)
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
                        print(
                            f"[STOP] Preserved audio file: {audio_file}",
                            file=sys.stderr,
                        )
            else:
                if "error" in result:
                    print(f"Error: {result['error']}", file=sys.stderr)

    except IpcUnavailable:
        print("Error: Cannot connect to voicepipe daemon. Is it running?", file=sys.stderr)
        print(
            "Start it with: systemctl --user start voicepipe.target",
            file=sys.stderr,
        )
        print("Or: voicepipe service start", file=sys.stderr)
        raise SystemExit(1)
    except IpcError as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
