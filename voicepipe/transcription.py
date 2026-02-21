"""Shared transcription helpers.

This centralizes the logic for:
- Using the transcriber daemon when available
- Falling back to direct OpenAI transcription when not
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import BinaryIO, Optional

from voicepipe.config import get_daemon_mode, get_transcribe_backend
from voicepipe.platform import is_windows
from voicepipe.paths import transcriber_socket_paths
from voicepipe.transcription_result import TranscriptionResult


class TranscriptionError(RuntimeError):
    pass


class TranscriberDaemonUnavailable(TranscriptionError):
    pass


def _normalize_backend(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"xi", "eleven", "eleven-labs"}:
        return "elevenlabs"
    return raw


def _resolve_backend_and_model(model: str) -> tuple[str, str, str]:
    """Return (backend, model_id, model_for_daemon)."""
    raw = (model or "").strip()
    if ":" in raw:
        maybe_backend, _sep, rest = raw.partition(":")
        backend = _normalize_backend(maybe_backend)
        model_id = rest.strip()
        if backend in {"openai", "elevenlabs"} and model_id:
            return backend, model_id, raw

    backend = _normalize_backend(get_transcribe_backend(load_env=True))
    model_id = raw
    model_for_daemon = raw
    if backend in {"openai", "elevenlabs"} and model_id:
        model_for_daemon = f"{backend}:{model_id}"
    return backend, model_id, model_for_daemon


def _transcribe_via_daemon(
    audio_file: str,
    *,
    model: str,
    language: Optional[str],
    prompt: Optional[str],
    temperature: float,
    socket_path: Optional[Path] = None,
    connect_timeout: float = 2.0,
    read_timeout: float = 60.0,
) -> str:
    sock_paths = [socket_path] if socket_path is not None else transcriber_socket_paths()
    existing_paths: list[Path] = []
    for p in sock_paths:
        try:
            if p.exists():
                existing_paths.append(p)
        except Exception:
            continue
    if not existing_paths:
        tried = ", ".join(str(p) for p in sock_paths)
        raise TranscriberDaemonUnavailable(f"Transcriber socket not found (tried: {tried})")

    last_error: Exception | None = None
    for sock_path in existing_paths:
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except OSError as e:
            raise TranscriberDaemonUnavailable(
                f"Unix sockets are unavailable on this platform: {e}"
            ) from e
        client.settimeout(connect_timeout)
        try:
            try:
                client.connect(str(sock_path))
            except OSError as e:
                last_error = e
                continue

            request = {"audio_file": audio_file, "model": model, "temperature": temperature}
            if language:
                request["language"] = language
            if prompt:
                request["prompt"] = prompt

            client.sendall((json.dumps(request) + "\n").encode("utf-8"))

            client.settimeout(read_timeout)

            buffer = ""
            full_text = ""
            while True:
                try:
                    chunk = client.recv(4096).decode("utf-8", errors="replace")
                except socket.timeout as e:
                    raise TranscriptionError(
                        f"Timed out waiting for transcriber daemon response ({sock_path})"
                    ) from e
                if not chunk:
                    break
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        response = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    kind = response.get("type")
                    if kind == "transcription":
                        text = response.get("text", "")
                        if text:
                            full_text += str(text)
                    elif kind == "complete":
                        return full_text
                    elif kind == "error":
                        raise TranscriptionError(
                            str(response.get("message") or "unknown error")
                        )

            return full_text
        finally:
            try:
                client.close()
            except Exception:
                pass

    msg = f"Could not connect to transcriber daemon (tried: {', '.join(str(p) for p in existing_paths)})"
    if last_error is not None:
        msg = f"{msg}: {last_error}"
    raise TranscriberDaemonUnavailable(msg)


def transcribe_audio_file(
    audio_file: str,
    *,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
    prefer_daemon: bool = True,
) -> str:
    """Transcribe an on-disk audio file."""
    backend, resolved_model, model_for_daemon = _resolve_backend_and_model(model)

    daemon_mode = get_daemon_mode(load_env=True)
    effective_prefer_daemon = bool(prefer_daemon)
    if daemon_mode == "never":
        effective_prefer_daemon = False
    elif daemon_mode == "auto" and is_windows():
        effective_prefer_daemon = False
    elif daemon_mode == "always":
        effective_prefer_daemon = True

    if effective_prefer_daemon:
        try:
            return _transcribe_via_daemon(
                os.path.abspath(os.path.expanduser(audio_file)),
                model=model_for_daemon,
                language=language,
                prompt=prompt,
                temperature=float(temperature),
            )
        except TranscriberDaemonUnavailable as e:
            if daemon_mode == "always":
                raise TranscriptionError(str(e)) from e
            pass

    if backend == "openai":
        from voicepipe.transcriber import WhisperTranscriber

        try:
            transcriber = WhisperTranscriber(model=resolved_model)
            return transcriber.transcribe(
                audio_file,
                language=language,
                prompt=prompt,
                temperature=float(temperature),
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    if backend == "elevenlabs":
        from voicepipe.elevenlabs_transcriber import ElevenLabsTranscriber

        try:
            transcriber = ElevenLabsTranscriber(model_id=resolved_model)
            return transcriber.transcribe(
                audio_file,
                language=language,
                prompt=prompt,
                temperature=float(temperature),
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    raise TranscriptionError(
        "Unsupported transcription backend.\n\n"
        "Set VOICEPIPE_TRANSCRIBE_BACKEND to one of: openai, elevenlabs\n"
        "Or prefix the model like: openai:whisper-1 or elevenlabs:scribe_v1\n"
        f"Got backend={backend!r} model={model!r}"
    )


def transcribe_audio_bytes(
    audio_bytes: bytes,
    *,
    filename: str,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
) -> str:
    """Transcribe audio bytes without requiring an on-disk temp file."""
    backend, resolved_model, _model_for_daemon = _resolve_backend_and_model(model)

    if backend == "openai":
        from voicepipe.transcriber import WhisperTranscriber

        try:
            transcriber = WhisperTranscriber(model=resolved_model)
            return transcriber.transcribe_bytes(
                audio_bytes,
                filename=str(filename or "audio.wav"),
                language=language,
                prompt=prompt,
                temperature=float(temperature),
                model=resolved_model,
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    if backend == "elevenlabs":
        from voicepipe.elevenlabs_transcriber import ElevenLabsTranscriber

        try:
            transcriber = ElevenLabsTranscriber(model_id=resolved_model)
            return transcriber.transcribe_bytes(
                audio_bytes,
                filename=str(filename or "audio.wav"),
                language=language,
                prompt=prompt,
                temperature=float(temperature),
                model=resolved_model,
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    raise TranscriptionError(
        "Unsupported transcription backend.\n\n"
        "Set VOICEPIPE_TRANSCRIBE_BACKEND to one of: openai, elevenlabs\n"
        "Or prefix the model like: openai:whisper-1 or elevenlabs:scribe_v1\n"
        f"Got backend={backend!r} model={model!r}"
    )


def transcribe_audio_fileobj(
    fh: BinaryIO,
    *,
    filename: str,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
) -> str:
    """Transcribe audio from a file-like object without a filesystem path."""
    backend, resolved_model, _model_for_daemon = _resolve_backend_and_model(model)

    if backend == "openai":
        from voicepipe.transcriber import WhisperTranscriber

        try:
            transcriber = WhisperTranscriber(model=resolved_model)
            return transcriber.transcribe_fileobj(
                fh,
                filename=str(filename or "audio.wav"),
                language=language,
                prompt=prompt,
                temperature=float(temperature),
                model=resolved_model,
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    if backend == "elevenlabs":
        from voicepipe.elevenlabs_transcriber import ElevenLabsTranscriber

        try:
            audio_bytes = fh.read()
            transcriber = ElevenLabsTranscriber(model_id=resolved_model)
            return transcriber.transcribe_bytes(
                audio_bytes,
                filename=str(filename or "audio.wav"),
                language=language,
                prompt=prompt,
                temperature=float(temperature),
                model=resolved_model,
            )
        except Exception as e:
            raise TranscriptionError(str(e)) from e

    raise TranscriptionError(
        "Unsupported transcription backend.\n\n"
        "Set VOICEPIPE_TRANSCRIBE_BACKEND to one of: openai, elevenlabs\n"
        "Or prefix the model like: openai:whisper-1 or elevenlabs:scribe_v1\n"
        f"Got backend={backend!r} model={model!r}"
    )


def transcribe_audio_file_result(
    audio_file: str,
    *,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
    prefer_daemon: bool = True,
    recording_id: str | None = None,
    source: str | None = None,
) -> TranscriptionResult:
    """Transcribe an on-disk audio file and return a structured result."""
    backend, resolved_model, _model_for_daemon = _resolve_backend_and_model(model)
    resolved_model = resolved_model or model
    text = transcribe_audio_file(
        audio_file,
        model=model,
        language=language,
        prompt=prompt,
        temperature=float(temperature),
        prefer_daemon=prefer_daemon,
    )
    return TranscriptionResult(
        text=text,
        backend=backend,
        model=resolved_model,
        audio_file=audio_file,
        recording_id=recording_id,
        source=source,
        warnings=[],
    )


def transcribe_audio_bytes_result(
    audio_bytes: bytes,
    *,
    filename: str,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
    recording_id: str | None = None,
    source: str | None = None,
) -> TranscriptionResult:
    """Transcribe audio bytes and return a structured result (no audio_file path)."""
    backend, resolved_model, _model_for_daemon = _resolve_backend_and_model(model)
    resolved_model = resolved_model or model
    text = transcribe_audio_bytes(
        audio_bytes,
        filename=filename,
        model=model,
        language=language,
        prompt=prompt,
        temperature=float(temperature),
    )
    return TranscriptionResult(
        text=text,
        backend=backend,
        model=resolved_model,
        audio_file=None,
        recording_id=recording_id,
        source=source,
        warnings=[],
    )


def transcribe_audio_fileobj_result(
    fh: BinaryIO,
    *,
    filename: str,
    model: str,
    language: Optional[str] = None,
    prompt: Optional[str] = None,
    temperature: float = 0.0,
    recording_id: str | None = None,
    source: str | None = None,
) -> TranscriptionResult:
    """Transcribe a file-like object and return a structured result (no audio_file path)."""
    backend, resolved_model, _model_for_daemon = _resolve_backend_and_model(model)
    resolved_model = resolved_model or model
    text = transcribe_audio_fileobj(
        fh,
        filename=filename,
        model=model,
        language=language,
        prompt=prompt,
        temperature=float(temperature),
    )
    return TranscriptionResult(
        text=text,
        backend=backend,
        model=resolved_model,
        audio_file=None,
        recording_id=recording_id,
        source=source,
        warnings=[],
    )
