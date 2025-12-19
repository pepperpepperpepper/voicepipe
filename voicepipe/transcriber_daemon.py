"""Persistent transcriber daemon with a pre-initialized OpenAI client.

This provides a simple newline-delimited JSON protocol over a Unix domain
socket. Clients send a single JSON object and receive streaming JSON lines.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import tempfile
import time
from pathlib import Path
from typing import Optional

from voicepipe.config import get_transcribe_model, load_environment
from voicepipe.logging_utils import configure_logging
from voicepipe.paths import runtime_app_dir, transcriber_socket_path
from voicepipe.transcriber import WhisperTranscriber

logger = logging.getLogger("voicepipe.transcriber_daemon")


def _unlink_if_exists(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _read_json_line(conn: socket.socket, max_bytes: int = 256_000) -> Optional[dict]:
    buffer = b""
    while b"\n" not in buffer:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buffer += chunk
        if len(buffer) > max_bytes:
            raise ValueError("request too large")
    if not buffer:
        return None
    line, _sep, _rest = buffer.partition(b"\n")
    if not line.strip():
        return None
    return json.loads(line.decode("utf-8"))


def _stream_text(conn: socket.socket, text: str) -> None:
    # Stream line-by-line to match existing clients.
    for line in text.split("\n"):
        if not line.strip():
            continue
        payload = {"type": "transcription", "text": line + "\n"}
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
    conn.sendall((json.dumps({"type": "complete"}) + "\n").encode("utf-8"))


def _send_error(conn: socket.socket, message: str) -> None:
    conn.sendall((json.dumps({"type": "error", "message": message}) + "\n").encode("utf-8"))


def serve(
    *,
    model: str = "gpt-4o-transcribe",
    socket_path: Optional[Path] = None,
) -> None:
    socket_file = socket_path or transcriber_socket_path(create_dir=True)
    tmp_dir = runtime_app_dir(create=True)

    transcriber = WhisperTranscriber(model=model)
    logger.info("Transcriber ready (model=%s)", model)

    _unlink_if_exists(socket_file)

    running = True

    def _stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(socket_file))
        try:
            os.chmod(socket_file, 0o600)
        except Exception:
            pass
        server.listen(1)
        server.settimeout(0.5)
        logger.info("Transcriber daemon listening on %s", socket_file)

        while running:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue

            try:
                conn.settimeout(300)
                request = _read_json_line(conn)
                if not request:
                    conn.close()
                    continue

                audio_file = request.get("audio_file")
                audio_hex = request.get("audio")
                suffix = request.get("suffix") or request.get("format") or ".wav"
                request_model = request.get("model")
                request_language = request.get("language")
                request_prompt = request.get("prompt")
                request_temperature = request.get("temperature")

                language = (
                    str(request_language)
                    if isinstance(request_language, str) and request_language.strip()
                    else None
                )
                prompt = (
                    str(request_prompt)
                    if isinstance(request_prompt, str) and request_prompt.strip()
                    else None
                )
                temp = float(request_temperature) if request_temperature is not None else 0.0
                req_model = (
                    str(request_model)
                    if isinstance(request_model, str) and request_model.strip()
                    else None
                )
                if isinstance(suffix, str) and suffix and not suffix.startswith("."):
                    suffix = "." + suffix

                if audio_hex:
                    audio_data = bytes.fromhex(audio_hex)
                    with tempfile.NamedTemporaryFile(
                        suffix=str(suffix),
                        delete=False,
                        dir=str(tmp_dir),
                    ) as tmp_file:
                        tmp_file.write(audio_data)
                        tmp_path = tmp_file.name
                    try:
                        start_time = time.time()
                        text = transcriber.transcribe(
                            tmp_path,
                            language=language,
                            prompt=prompt,
                            temperature=temp,
                            model=req_model,
                        )
                        logger.info(
                            "Transcribed hex audio in %.2fs (%s)",
                            time.time() - start_time,
                            tmp_path,
                        )
                        _stream_text(conn, text)
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except Exception:
                            pass
                elif isinstance(audio_file, str) and os.path.exists(audio_file):
                    start_time = time.time()
                    text = transcriber.transcribe(
                        audio_file,
                        language=language,
                        prompt=prompt,
                        temperature=temp,
                        model=req_model,
                    )
                    logger.info(
                        "Transcribed file in %.2fs (%s)",
                        time.time() - start_time,
                        audio_file,
                    )
                    _stream_text(conn, text)
                else:
                    _send_error(conn, "Audio file not found")
            except json.JSONDecodeError:
                _send_error(conn, "Invalid JSON")
            except Exception as e:
                logger.exception("Transcriber request error: %s", e)
                try:
                    _send_error(conn, str(e))
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
    finally:
        try:
            server.close()
        except Exception:
            pass
        _unlink_if_exists(socket_file)


def main(argv: Optional[list[str]] = None) -> None:
    # Keep it simple: configuration via env vars.
    del argv
    configure_logging(default_level=logging.INFO)
    load_environment()
    model = get_transcribe_model()
    logger.info("Initializing transcriber (model=%s)...", model)
    serve(model=model)


if __name__ == "__main__":
    main()
