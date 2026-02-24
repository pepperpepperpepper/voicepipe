from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from voicepipe.config import get_transcribe_model
from voicepipe.last_output import save_last_output
from voicepipe.paths import preserved_audio_dir
from voicepipe.transcription import transcribe_audio_file_result


logger = logging.getLogger(__name__)


def transcribe_timeout_audio_file(
    audio_file: str,
    *,
    recording_id: str | None,
    source: str,
    keep_audio: bool = False,
) -> bool:
    """Transcribe an auto-stopped (timeout) recording and buffer it for replay.

    Returns True when transcription succeeded and the last-output buffer was
    updated. On failure, the audio file is preserved (best-effort) so it can be
    retried later.
    """
    resolved_model = (get_transcribe_model(load_env=True) or "").strip()
    try:
        result = transcribe_audio_file_result(
            audio_file,
            model=resolved_model,
            prefer_daemon=True,
            recording_id=recording_id,
            source=source,
        )
    except Exception as e:
        logger.exception("Timeout transcription failed: %s", e)
        _preserve_audio_file(audio_file)
        return False

    output_text = (result.text or "").rstrip("\n")
    payload = result.to_dict()
    payload["timeout"] = True

    try:
        save_last_output(output_text, payload=payload)
    except Exception as e:
        logger.exception("Failed to save replay buffer for timeout transcription: %s", e)
        _preserve_audio_file(audio_file)
        return False

    if not keep_audio:
        try:
            if audio_file and os.path.exists(audio_file):
                os.unlink(audio_file)
        except Exception:
            pass

    return True


def _preserve_audio_file(audio_file: str) -> str:
    if not audio_file:
        return ""
    try:
        if not os.path.exists(audio_file):
            return ""
    except Exception:
        return ""

    try:
        dst_dir = preserved_audio_dir(create=True)
        dst = dst_dir / Path(audio_file).name
        shutil.move(audio_file, dst)
        logger.warning("Preserved timed-out audio file: %s", dst)
        return str(dst)
    except Exception:
        return ""

