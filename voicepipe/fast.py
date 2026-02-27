"""Fast voicepipe control with optimal transcription handling.

This is intended for use from window-manager hotkeys where startup latency and
stderr visibility matter.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import BinaryIO, Optional

from voicepipe.config import get_transcribe_model, load_environment
from voicepipe.locks import LockHeld, PidFileLock
from voicepipe.paths import logs_dir, preserved_audio_dir, runtime_app_dir
from voicepipe.platform import is_linux, is_macos, is_windows
from voicepipe.recording_backend import AutoRecorderBackend, RecordingError


DEBOUNCE_MS = 500  # milliseconds

_LOG_FH = None
_INPROCESS_RECORDING: dict[str, object] = {}
_RUNTIME_DIR: Path | None = None
_RUNTIME_DIR_CREATED = False
_RUNTIME_DIR_LOCK = threading.Lock()
_INPROCESS_TOGGLE_LOCK = threading.Lock()
_INPROCESS_LAST_DEBOUNCE_MS: int | None = None


@dataclass(frozen=True)
class _TogglePostStop:
    audio_file: str
    target_window: str | None
    typing_backend: object


def _runtime_dir(*, create: bool) -> Path:
    global _RUNTIME_DIR
    global _RUNTIME_DIR_CREATED

    cached = _RUNTIME_DIR
    if cached is not None and (not create or _RUNTIME_DIR_CREATED):
        return cached

    with _RUNTIME_DIR_LOCK:
        cached = _RUNTIME_DIR
        if cached is not None and (not create or _RUNTIME_DIR_CREATED):
            return cached

        resolved = runtime_app_dir(create=create)
        _RUNTIME_DIR = resolved
        if create:
            _RUNTIME_DIR_CREATED = True
        return resolved


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

    if is_macos():
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
    from voicepipe.wav import read_wav_duration_s

    try:
        duration_s = read_wav_duration_s(audio_file)
        if duration_s is not None and duration_s < 0.2:
            fast_log(f"[TRANSCRIBE] Skipping transcription (audio too short: {duration_s:.3f}s)")
            return ""
        model = get_transcribe_model()
        return transcribe_audio_file(
            audio_file,
            model=model,
            prefer_daemon=True,
        )
    except Exception as e:
        fast_log(f"[TRANSCRIBE] Error: {e}")
        return ""


def send_transcribe_request_result(audio_file: str, *, source: str) -> "TranscriptionResult":
    """Transcribe audio and return a structured result including trigger metadata."""
    # Keep imports out of the `start` hot path.
    from voicepipe.transcription import transcribe_audio_file_result
    from voicepipe.transcription_result import TranscriptionResult
    from voicepipe.wav import read_wav_duration_s

    try:
        duration_s = read_wav_duration_s(audio_file)
        if duration_s is not None and duration_s < 0.2:
            fast_log(
                f"[TRANSCRIBE] Skipping transcription (audio too short: {duration_s:.3f}s)"
            )
            return TranscriptionResult(
                text="",
                backend="",
                model=get_transcribe_model(),
                audio_file=audio_file,
                source=source,
                warnings=[],
            )
        model = get_transcribe_model()
        return transcribe_audio_file_result(
            audio_file,
            model=model,
            prefer_daemon=True,
            apply_triggers=True,
            source=source,
        )
    except Exception as e:
        fast_log(f"[TRANSCRIBE] Error: {e}")
        return TranscriptionResult(
            text="",
            backend="",
            model=get_transcribe_model(),
            audio_file=audio_file,
            source=source,
            warnings=[],
        )

def _is_execute_trigger(result: object) -> bool:
    trigger = getattr(result, "transcript_trigger", None)
    if not isinstance(trigger, dict):
        return False

    meta = trigger.get("meta")
    if not isinstance(meta, dict):
        return False
    return str(meta.get("verb_type") or "").strip().lower() == "execute"


def _extract_type_sequence(result: object) -> list[dict[str, object]] | None:
    trigger = getattr(result, "transcript_trigger", None)
    if not isinstance(trigger, dict):
        return None

    action = str(trigger.get("action") or "").strip().lower()
    meta = trigger.get("meta")
    if not isinstance(meta, dict):
        return None

    if action == "type":
        seq = meta.get("sequence")
        return seq if isinstance(seq, list) else None

    if action != "dispatch":
        return None
    if str(meta.get("action") or "").strip().lower() != "type":
        return None
    handler_meta = meta.get("handler_meta")
    if not isinstance(handler_meta, dict):
        return None
    seq = handler_meta.get("sequence")
    return seq if isinstance(seq, list) else None


def _extract_type_sequence_from_payload(payload: object) -> list[dict[str, object]] | None:
    if not isinstance(payload, dict):
        return None

    action = str(payload.get("action") or "").strip().lower()
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None

    if action == "type":
        seq = meta.get("sequence")
        return seq if isinstance(seq, list) else None

    if action != "dispatch":
        return None
    if str(meta.get("action") or "").strip().lower() != "type":
        return None
    handler_meta = meta.get("handler_meta")
    if not isinstance(handler_meta, dict):
        return None
    seq = handler_meta.get("sequence")
    return seq if isinstance(seq, list) else None

def send_transcribe_request_fileobj(fh: BinaryIO, *, filename: str) -> str:
    """Transcribe audio from a file-like object without writing a temp WAV."""
    # Keep imports out of the `start` hot path.
    from voicepipe.transcription import transcribe_audio_fileobj

    try:
        model = get_transcribe_model()
        return transcribe_audio_fileobj(
            fh,
            filename=str(filename or "audio.wav"),
            model=model,
            temperature=0.0,
        )
    except Exception as e:
        fast_log(f"[TRANSCRIBE] Error: {e}")
        return ""


def send_transcribe_request_bytes(audio_bytes: bytes, *, filename: str) -> str:
    """Transcribe audio bytes (e.g. WAV) without writing a temp file."""
    # Keep imports out of hot paths.
    from voicepipe.transcription import transcribe_audio_bytes

    try:
        model = get_transcribe_model()
        return transcribe_audio_bytes(
            audio_bytes,
            filename=str(filename or "audio.wav"),
            model=model,
            temperature=0.0,
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
    execute_toggle_split(perform_transcribe=True)


def execute_toggle_split(*, perform_transcribe: bool) -> _TogglePostStop | None:
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
            post = _TogglePostStop(
                audio_file=audio_file,
                target_window=target_window,
                typing_backend=typing_backend,
            )
            if not perform_transcribe:
                return post

            _perform_toggle_post_stop(post)
        else:
            fast_log("[TOGGLE] Starting recording...")
            # Start recording
            backend.start(device=None)
            return None

        return None
    except RecordingError as e:
        fast_log(f"[TOGGLE] Recording error: {e}")
        raise SystemExit(1)
    except Exception as e:
        fast_log(f"[TOGGLE] Unexpected error: {e}")
        raise


def _perform_toggle_post_stop(post: _TogglePostStop) -> None:
    # Keep imports out of hot paths; the toggle lock has already been released.
    from voicepipe.typing import perform_type_sequence, press_enter, type_text

    audio_file = post.audio_file
    target_window = post.target_window
    typing_backend = post.typing_backend

    result = send_transcribe_request_result(audio_file, source="fast-toggle")
    transcription_ok = False
    if result.text:
        output_text = (result.text or "").rstrip()
        fast_log(f"[TOGGLE] Transcription: {output_text}")
        trigger_meta = result.transcript_trigger
        if trigger_meta is not None:
            fast_log(f"[TOGGLE] Transcript trigger: {trigger_meta}")

        # Always persist the last output for replay/recovery workflows.
        try:
            from voicepipe.last_output import save_last_output

            payload = result.to_dict()
            payload["output_text"] = output_text
            save_last_output(output_text, payload=payload)
        except Exception:
            pass

        type_sequence = _extract_type_sequence(result)
        if type_sequence is not None:
            typed_ok, type_err = perform_type_sequence(
                type_sequence,
                window_id=target_window,
                backend=typing_backend,  # type: ignore[arg-type]
            )
        else:
            typed_ok, type_err = type_text(
                output_text,
                window_id=target_window,
                backend=typing_backend,  # type: ignore[arg-type]
            )
        if not typed_ok:
            fast_log(f"[TOGGLE] Warning: typing failed: {type_err}")
        elif type_sequence is None and _is_execute_trigger(result) and output_text.strip():
            ok2, err2 = press_enter(
                window_id=target_window,
                backend=typing_backend,  # type: ignore[arg-type]
            )
            if not ok2:
                fast_log(f"[TOGGLE] Warning: could not press Enter: {err2}")
        transcription_ok = True
    else:
        fast_log("[TOGGLE] No transcription returned")

    if transcription_ok:
        if os.path.exists(audio_file):
            os.unlink(audio_file)
            fast_log(f"[TOGGLE] Cleaned up audio file: {audio_file}")
        return

    try:
        dst_dir = preserved_audio_dir(create=True)
        dst = dst_dir / Path(audio_file).name
        shutil.move(audio_file, dst)
        audio_file = str(dst)
    except Exception:
        pass
    fast_log(f"[TOGGLE] Preserved audio file: {audio_file}")


def _inprocess_is_recording() -> bool:
    # Treat the presence of a recorder object as authoritative state for the
    # in-process hotkey runner. On some Windows environments, the `recording`
    # flag can end up falsey even though the stream is still active.
    return _INPROCESS_RECORDING.get("recorder") is not None


def _inprocess_start() -> None:
    from voicepipe.audio import resolve_audio_input_for_recording, select_audio_input, write_device_cache
    from voicepipe.config import get_audio_channels, get_audio_sample_rate
    from voicepipe.recorder import AudioRecorder

    if _inprocess_is_recording():
        return

    preferred_samplerate = get_audio_sample_rate()
    preferred_channels = get_audio_channels()

    t0 = time.monotonic()
    resolution = resolve_audio_input_for_recording(
        preferred_samplerate=preferred_samplerate,
        preferred_channels=preferred_channels,
    )
    t1 = time.monotonic()
    selection = resolution.selection

    def _start_with_selection(sel) -> tuple[AudioRecorder, float]:
        rec = AudioRecorder(
            device_index=sel.device_index,
            sample_rate=sel.samplerate,
            channels=sel.channels,
            max_duration=None,
        )
        t_start = time.monotonic()
        rec.start_recording(output_file=None)
        return rec, t_start

    start_selection = selection
    recorder: AudioRecorder | None = None
    t2 = time.monotonic()
    t3 = t2
    try:
        try:
            recorder, t2 = _start_with_selection(selection)
            t3 = time.monotonic()
        except Exception as e:
            fast_log(
                f"[TOGGLE] In-process start failed (source={getattr(resolution, 'source', '')}): {e}"
            )
            try:
                if recorder:
                    recorder.cleanup()
            except Exception:
                pass

            strict = str(getattr(resolution, "source", "")).startswith("config-")
            start_selection = select_audio_input(
                preferred_device_index=selection.device_index,
                preferred_samplerate=preferred_samplerate,
                preferred_channels=preferred_channels,
                strict_device_index=strict,
            )
            fast_log(
                "[TOGGLE] In-process retry selection: "
                f"device={start_selection.device_index} samplerate={start_selection.samplerate} "
                f"channels={start_selection.channels}"
            )

            recorder, t2 = _start_with_selection(start_selection)
            t3 = time.monotonic()

            try:
                import sounddevice as sd  # type: ignore

                name = str(sd.query_devices(int(start_selection.device_index)).get("name", ""))
            except Exception:
                name = ""
            try:
                write_device_cache(start_selection, device_name=name, source="auto")
            except Exception:
                pass
    except Exception:
        try:
            if recorder:
                recorder.cleanup()
        except Exception:
            pass
        raise

    assert recorder is not None

    prior = _INPROCESS_RECORDING.get("recorder")
    if prior is not None:
        try:
            cleanup = getattr(prior, "cleanup", None)
            if callable(cleanup):
                cleanup()
        except Exception:
            pass

    _INPROCESS_RECORDING.clear()
    _INPROCESS_RECORDING.update(
        {
            "recorder": recorder,
            "resolution_source": str(getattr(resolution, "source", "")),
            "recording_id": str(int(time.time() * 1000)),
        }
    )

    fast_log(
        "[TOGGLE] In-process recording started: "
        f"device={start_selection.device_index} samplerate={start_selection.samplerate} "
        f"channels={start_selection.channels} source={getattr(resolution, 'source', '')}"
    )
    fast_log(
        "[TOGGLE] In-process timing: "
        f"resolve_ms={int((t1 - t0) * 1000)} start_ms={int((t3 - t2) * 1000)}"
    )


def _inprocess_stop() -> tuple[bytes, int, int, str]:
    from voicepipe.recorder import AudioRecorder

    recorder = _INPROCESS_RECORDING.get("recorder")
    recording_id = str(_INPROCESS_RECORDING.get("recording_id") or "")
    _INPROCESS_RECORDING.clear()

    if not isinstance(recorder, AudioRecorder):
        raise RecordingError("No in-process recording in progress")

    samplerate = int(getattr(recorder, "rate", 0) or 0)
    channels = int(getattr(recorder, "channels", 0) or 0)

    audio_data: bytes | None = None
    try:
        audio_data = recorder.stop_recording()
    finally:
        try:
            recorder.cleanup()
        except Exception:
            pass

    if not audio_data:
        # If `stop_recording()` returns None (e.g. if it thought recording was
        # already stopped), try to salvage any frames captured so far.
        try:
            frames: list[bytes] = []
            q = getattr(recorder, "audio_queue", None)
            if q is not None:
                while not q.empty():
                    frames.append(q.get())
            if frames:
                audio_data = b"".join(frames)
        except Exception:
            audio_data = None

    if not audio_data:
        raise RecordingError("No audio data recorded (in-process)")

    if not recording_id:
        recording_id = str(int(time.time() * 1000))
    return audio_data, samplerate, channels, recording_id


def execute_toggle_inprocess() -> None:
    """Windows hotkey path: record/stop in-process (avoids subprocess cold-start)."""
    try:
        fast_log("[TOGGLE] Starting toggle execution (in-process)")
        try:
            rec = _INPROCESS_RECORDING.get("recorder")
            fast_log(
                "[TOGGLE] In-process state: "
                f"recorder={'set' if rec is not None else 'none'} "
                f"recording={getattr(rec, 'recording', None) if rec is not None else None} "
                f"stream={'set' if getattr(rec, 'stream', None) is not None else 'none'}"
            )
        except Exception:
            pass

        if _inprocess_is_recording():
            from voicepipe.typing import (
                get_active_window_id,
                perform_type_sequence,
                press_enter,
                resolve_typing_backend,
                type_text,
            )

            typing_backend = resolve_typing_backend()
            target_window = None
            if typing_backend.supports_window_id:
                target_window = get_active_window_id()
                if target_window:
                    fast_log(f"[TOGGLE] Target window: {target_window}")
                else:
                    fast_log("[TOGGLE] Target window: (unknown)")

            fast_log("[TOGGLE] Recording active (in-process), stopping...")
            pcm, samplerate, channels, recording_id = _inprocess_stop()

            from voicepipe.wav import pcm_duration_s, write_wav_pcm

            duration_s = pcm_duration_s(pcm, sample_rate=samplerate, channels=channels)
            if duration_s is not None and duration_s < 0.2:
                fast_log(
                    f"[TRANSCRIBE] Skipping transcription (audio too short: {duration_s:.3f}s)"
                )
                return

            if samplerate <= 0 or channels <= 0:
                raise RecordingError(
                    f"Invalid audio params (in-process): samplerate={samplerate} channels={channels}"
                )

            import io

            wav_fh = io.BytesIO()
            wav_bytes: bytes = b""
            try:
                try:
                    write_wav_pcm(
                        wav_fh,
                        pcm,
                        sample_rate=samplerate,
                        channels=channels,
                        sample_width=2,
                    )
                    wav_bytes = wav_fh.getvalue()
                    fast_log(
                        "[TOGGLE] In-process audio: "
                        f"pcm_bytes={len(pcm)} wav_bytes={len(wav_bytes)} "
                        f"duration_s={(f'{duration_s:.3f}' if duration_s is not None else 'unknown')} "
                        f"samplerate={samplerate} channels={channels}"
                    )
                    t_tr0 = time.monotonic()
                    text = send_transcribe_request_bytes(wav_bytes, filename="audio.wav")
                    fast_log(f"[TRANSCRIBE] Completed in {int((time.monotonic() - t_tr0) * 1000)}ms")
                except Exception as e:
                    fast_log(f"[TRANSCRIBE] Error: {e}")
                    text = ""

                transcription_ok = False
                if text:
                    cleaned_text = text.rstrip()
                    fast_log(f"[TOGGLE] Transcription: {cleaned_text}")
                    try:
                        try:
                            last_path = _runtime_dir(create=True) / "voicepipe-last.txt"
                        except Exception as e:
                            last_path = None
                            fast_log(f"[TOGGLE] Warning: could not resolve last transcript path: {e}")

                        if last_path is not None:
                            try:
                                fast_log(f"[TOGGLE] Writing last transcript: {last_path}")
                                last_path.write_text(cleaned_text + "\n", encoding="utf-8")
                                fast_log("[TOGGLE] Wrote last transcript")
                            except PermissionError:
                                # Stale file created by an elevated process.
                                try:
                                    last_path.unlink()
                                except Exception:
                                    pass
                                try:
                                    last_path.write_text(
                                        cleaned_text + "\n", encoding="utf-8"
                                    )
                                    fast_log("[TOGGLE] Wrote last transcript (recreated)")
                                except Exception:
                                    try:
                                        alt = last_path.with_name(
                                            f"voicepipe-last-{int(time.time() * 1000)}.txt"
                                        )
                                        alt.write_text(cleaned_text + "\n", encoding="utf-8")
                                        fast_log(f"[TOGGLE] Wrote last transcript: {alt}")
                                    except Exception as e:
                                        fast_log(
                                            f"[TOGGLE] Warning: could not write last transcript: {e}"
                                        )
                            except Exception as e:
                                fast_log(f"[TOGGLE] Warning: could not write last transcript: {e}")
                    except Exception as e:
                        fast_log(f"[TOGGLE] Warning: unexpected error persisting last transcript: {e}")

                    from voicepipe.transcript_triggers import apply_transcript_triggers

                    output_text, trigger_meta = apply_transcript_triggers(cleaned_text)
                    if trigger_meta is not None:
                        fast_log(f"[TOGGLE] Transcript trigger: {trigger_meta}")

                    # Persist last output for replay/recovery workflows.
                    try:
                        from voicepipe.last_output import save_last_output

                        payload = {
                            "source": "fast-toggle-inprocess",
                            "text": cleaned_text,
                            "trigger_meta": trigger_meta,
                            "output_text": output_text,
                        }
                        save_last_output(output_text, payload=payload)
                    except Exception:
                        pass

                    fast_log(
                        "[TOGGLE] Typing transcription: "
                        f"backend={typing_backend.name} window_id={target_window or ''} chars={len(output_text)}"
                    )
                    t_type0 = time.monotonic()
                    type_sequence = _extract_type_sequence_from_payload(trigger_meta)
                    if type_sequence is not None:
                        typed_ok, type_err = perform_type_sequence(
                            type_sequence,
                            window_id=target_window,
                            backend=typing_backend,
                        )
                    else:
                        typed_ok, type_err = type_text(
                            output_text,
                            window_id=target_window,
                            backend=typing_backend,
                        )
                    t_type_ms = int((time.monotonic() - t_type0) * 1000)
                    if not typed_ok:
                        fast_log(f"[TOGGLE] Warning: typing failed: {type_err}")
                    else:
                        fast_log(f"[TOGGLE] Typed transcription (ok) in {t_type_ms}ms")
                        if (
                            type_sequence is None
                            and
                            isinstance(trigger_meta, dict)
                            and isinstance(trigger_meta.get("meta"), dict)
                            and str(trigger_meta["meta"].get("verb_type") or "").strip().lower()
                            == "execute"
                            and output_text.strip()
                        ):
                            ok2, err2 = press_enter(
                                window_id=target_window,
                                backend=typing_backend,
                            )
                            if not ok2:
                                fast_log(f"[TOGGLE] Warning: could not press Enter: {err2}")
                    transcription_ok = True
                else:
                    fast_log("[TOGGLE] No transcription returned")

                if not transcription_ok:
                    try:
                        dst_dir = preserved_audio_dir(create=True)
                        dst = dst_dir / f"voicepipe_{recording_id}.wav"
                        with open(dst, "wb") as out:
                            out.write(wav_bytes)
                        fast_log(f"[TOGGLE] Preserved audio file: {dst}")
                    except Exception as e:
                        fast_log(f"[TOGGLE] Failed to preserve audio: {e}")

                return
            finally:
                try:
                    wav_fh.close()  # type: ignore[attr-defined]
                except Exception:
                    pass

        fast_log("[TOGGLE] Starting recording (in-process)...")
        _inprocess_start()

    except RecordingError as e:
        fast_log(f"[TOGGLE] Recording error: {e}")
        raise SystemExit(1)
    except Exception as e:
        fast_log(f"[TOGGLE] Unexpected error: {e}")
        raise


def toggle_inprocess_main(argv: Optional[list[str]] = None) -> None:
    """Entry point for the Windows hotkey runner.

    This behaves like `voicepipe-fast toggle`, but keeps recording in-process to
    avoid subprocess cold-start latency.
    """
    del argv

    load_environment(load_cwd_dotenv=False)

    try:
        fast_log("[MAIN] Toggle command received (in-process)")
        if not _INPROCESS_TOGGLE_LOCK.acquire(blocking=False):
            fast_log("[MAIN] Toggle lock already held, exiting")
            raise SystemExit(0)
        try:
            try:
                import faulthandler

                fh = _open_log()
                if fh is not None:
                    # If the hotkey runner hangs (common failure mode on Windows
                    # desktops), dump thread stacks into the log to pinpoint
                    # the blocking call.
                    faulthandler.dump_traceback_later(30.0, repeat=False, file=fh)
            except Exception:
                faulthandler = None  # type: ignore[assignment]

            global _INPROCESS_LAST_DEBOUNCE_MS
            now = int(time.time() * 1000)
            last = _INPROCESS_LAST_DEBOUNCE_MS
            if last is not None and now - int(last) < DEBOUNCE_MS:
                fast_log(
                    f"[DEBOUNCE] Skipping (in-process) - last: {last}, current: {now}, "
                    f"diff: {now - int(last)}ms"
                )
                raise SystemExit(0)
            _INPROCESS_LAST_DEBOUNCE_MS = now
            fast_log(f"[DEBOUNCE] Allowing (in-process) - current: {now}")

            fast_log("[MAIN] Executing toggle (in-process)")
            execute_toggle_inprocess()
            fast_log("[MAIN] Toggle completed")
            return
        finally:
            try:
                if "faulthandler" in locals() and faulthandler is not None:
                    try:
                        faulthandler.cancel_dump_traceback_later()
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                _INPROCESS_TOGGLE_LOCK.release()
            except Exception:
                pass
    except LockHeld as e:
        fast_log(f"[MAIN] {e}")
        raise SystemExit(0)


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
        post = None
        try:
            fast_log("[MAIN] Toggle command received")
            with PidFileLock(_lock_path(create_dir=True)):
                fast_log("[MAIN] Lock acquired")
                # Also check debounce inside the lock to prevent rapid toggles
                if not check_debounce():
                    fast_log("[MAIN] Debounced, exiting")
                    raise SystemExit(0)  # Exit silently if debounced
                fast_log("[MAIN] Executing toggle")
                post = execute_toggle_split(perform_transcribe=False)
        except LockHeld as e:
            fast_log(f"[MAIN] {e}")
            # Another instance is running, exit silently
            raise SystemExit(0)

        # Release the toggle lock before transcription so hotkeys don't feel "down"
        # when OpenAI requests take a long time.
        if post is not None:
            _perform_toggle_post_stop(post)
        fast_log("[MAIN] Toggle completed")
        return

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
                result = send_transcribe_request_result(audio_file, source="fast-stop")
                output_text = (result.text or "").rstrip()
                # Persist last output for replay/recovery workflows.
                try:
                    from voicepipe.last_output import save_last_output

                    payload = result.to_dict()
                    payload["output_text"] = output_text
                    save_last_output(output_text, payload=payload)
                except Exception:
                    pass
                # Output text
                if output_text:
                    out = getattr(sys, "stdout", None)
                    if out is not None:
                        print(output_text, file=out)
                    else:
                        fast_log(output_text)
                # Clean up
                if output_text and os.path.exists(audio_file):
                    os.unlink(audio_file)
                elif not output_text:
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
