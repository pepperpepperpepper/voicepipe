"""Subprocess entrypoint used for non-daemon recording mode.

This exists so recording can run as a separate process that can be stopped via
signals while keeping the top-level Click CLI responsive.
"""

from __future__ import annotations


def run_recording_subprocess() -> None:
    import os
    import signal
    import sys
    import threading

    from voicepipe.audio import select_audio_input
    from voicepipe.config import get_audio_channels, get_audio_sample_rate
    from voicepipe.recorder import AudioRecorder
    from voicepipe.session import RecordingSession

    recorder = None
    session = None
    timeout_timer = None
    try:
        session = RecordingSession.create_session()

        def _cleanup_session() -> None:
            try:
                RecordingSession.cleanup_session(session)
            except Exception:
                pass

        def stop_handler(signum, frame) -> None:
            """Stop recording and save audio."""
            if timeout_timer:
                try:
                    timeout_timer.cancel()
                except Exception:
                    pass
            if recorder and recorder.recording:
                try:
                    audio_data = recorder.stop_recording()
                    if audio_data:
                        recorder.save_to_file(audio_data, session["audio_file"])
                except Exception as e:
                    print(f"Error saving audio: {e}", file=sys.stderr)
            if recorder:
                recorder.cleanup()
            _cleanup_session()
            raise SystemExit(0)

        def cancel_handler(signum, frame) -> None:
            """Cancel recording without saving audio."""
            if timeout_timer:
                try:
                    timeout_timer.cancel()
                except Exception:
                    pass
            if recorder and recorder.recording:
                try:
                    recorder.stop_recording()
                except Exception:
                    pass
            if recorder:
                recorder.cleanup()
            try:
                audio_file = session.get("audio_file") if session else None
                if audio_file and os.path.exists(audio_file):
                    os.unlink(audio_file)
            except Exception:
                pass
            _cleanup_session()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop_handler)
        signal.signal(signal.SIGINT, cancel_handler)
        try:
            signal.signal(signal.SIGUSR1, cancel_handler)
        except Exception:
            pass

        device = os.environ.get("VOICEPIPE_DEVICE")
        device_index = int(device) if device and device.isdigit() else None

        selection = select_audio_input(
            preferred_device_index=device_index,
            preferred_samplerate=get_audio_sample_rate(),
            preferred_channels=get_audio_channels(),
            strict_device_index=bool(device_index is not None),
        )
        recorder = AudioRecorder(
            device_index=selection.device_index,
            sample_rate=selection.samplerate,
            channels=selection.channels,
            max_duration=None,
        )

        print(f"Recording started (PID: {os.getpid()})...", file=sys.stderr)
        recorder.start_recording(output_file=session["audio_file"])

        def _timeout_kill() -> None:
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                pass

        timeout_timer = threading.Timer(300, _timeout_kill)
        timeout_timer.daemon = True
        timeout_timer.start()

        signal.pause()

    except SystemExit:
        raise
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if recorder:
            recorder.cleanup()
        raise SystemExit(1)
