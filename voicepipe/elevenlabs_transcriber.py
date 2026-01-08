"""ElevenLabs Speech-to-Text API integration."""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from uuid import uuid4

from voicepipe.config import get_elevenlabs_api_key


ELEVENLABS_SPEECH_TO_TEXT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def _encode_multipart_form(
    *,
    fields: dict[str, str],
    files: dict[str, tuple[str, bytes, str]],
    boundary: str,
) -> bytes:
    crlf = "\r\n"
    out: list[bytes] = []

    for name, value in fields.items():
        out.append(f"--{boundary}{crlf}".encode("utf-8"))
        out.append(
            (
                f'Content-Disposition: form-data; name="{name}"{crlf}{crlf}{value}{crlf}'
            ).encode("utf-8")
        )

    for field_name, (filename, data, content_type) in files.items():
        out.append(f"--{boundary}{crlf}".encode("utf-8"))
        out.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"{crlf}'
                f"Content-Type: {content_type}{crlf}{crlf}"
            ).encode("utf-8")
        )
        out.append(data)
        out.append(crlf.encode("utf-8"))

    out.append(f"--{boundary}--{crlf}".encode("utf-8"))
    return b"".join(out)


class ElevenLabsTranscriber:
    """Handles transcription using ElevenLabs Speech-to-Text."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model_id: str = "scribe_v1",
        timeout: float = 60.0,
        base_url: str = ELEVENLABS_SPEECH_TO_TEXT_URL,
    ) -> None:
        self.api_key = api_key or get_elevenlabs_api_key()
        self.model_id = model_id
        self.timeout = float(timeout)
        self.base_url = (base_url or "").strip() or ELEVENLABS_SPEECH_TO_TEXT_URL

    def transcribe(
        self,
        audio_file: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        path = Path(audio_file)
        if not path.exists():
            raise RuntimeError(f"Audio file not found: {audio_file}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return self.transcribe_bytes(
            path.read_bytes(),
            filename=path.name,
            content_type=content_type,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            model=model,
        )

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str | None = None,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        # ElevenLabs STT does not currently support prompt/temperature.
        del prompt, temperature

        model_id = (model or self.model_id or "").strip()
        if not model_id:
            raise RuntimeError("ElevenLabs model_id is required")

        resolved_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

        boundary = f"voicepipe-{uuid4().hex}"
        fields: dict[str, str] = {"model_id": model_id}
        if language:
            fields["language_code"] = str(language)

        body = _encode_multipart_form(
            fields=fields,
            files={"file": (str(filename or "audio.wav"), audio_bytes, resolved_type)},
            boundary=boundary,
        )

        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # ElevenLabs uses xi-api-key for auth.
            "xi-api-key": self.api_key,
        }

        request = urllib.request.Request(
            self.base_url,
            method="POST",
            headers=headers,
            data=body,
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = (e.read() or b"").decode("utf-8", errors="replace")
            except Exception:
                body = ""
            raise RuntimeError(
                f"ElevenLabs transcription failed (HTTP {getattr(e, 'code', '?')}): {body}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"ElevenLabs transcription failed: {e}") from e

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            snippet = raw[:200].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ElevenLabs transcription returned non-JSON: {snippet}"
            ) from e

        text = payload.get("text")
        if not isinstance(text, str):
            raise RuntimeError(f"ElevenLabs response missing text: {payload}")
        return text.strip()
