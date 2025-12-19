"""Shared transcription helpers.

This centralizes the logic for:
- Using the transcriber daemon when available
- Falling back to direct OpenAI transcription when not
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Optional

from voicepipe.paths import transcriber_socket_path


class TranscriptionError(RuntimeError):
    pass


class TranscriberDaemonUnavailable(TranscriptionError):
    pass


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
    sock_path = socket_path or transcriber_socket_path()
    if not sock_path.exists():
        raise TranscriberDaemonUnavailable(f"Transcriber socket not found: {sock_path}")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(connect_timeout)
    try:
        try:
            client.connect(str(sock_path))
        except OSError as e:
            raise TranscriberDaemonUnavailable(
                f"Could not connect to transcriber daemon at {sock_path}: {e}"
            ) from e

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
                    raise TranscriptionError(str(response.get("message") or "unknown error"))

        return full_text
    finally:
        try:
            client.close()
        except Exception:
            pass


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
    if prefer_daemon:
        try:
            return _transcribe_via_daemon(
                audio_file,
                model=model,
                language=language,
                prompt=prompt,
                temperature=float(temperature),
            )
        except TranscriberDaemonUnavailable:
            pass

    from voicepipe.transcriber import WhisperTranscriber

    try:
        transcriber = WhisperTranscriber(model=model)
        return transcriber.transcribe(
            audio_file,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
        )
    except Exception as e:
        raise TranscriptionError(str(e)) from e
