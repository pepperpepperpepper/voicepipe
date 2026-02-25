"""OpenAI Whisper API integration for transcription."""

from __future__ import annotations

from typing import BinaryIO
from typing import Optional

from voicepipe.config import get_openai_api_key

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = e
else:
    _OPENAI_IMPORT_ERROR = None


class WhisperTranscriber:
    """Handles transcription using OpenAI's Whisper API."""
    
    # Default prompts for different models
    WHISPER_PROMPT = 'She said, "Hello, how are you?" Then she asked, "What\'s your name?" I replied, "My name is John."'
    
    GPT4_PROMPT = """Please transcribe in dictation mode. When the speaker says punctuation commands, convert them to actual punctuation:
- "open quote" or "quotation mark" → "
- "close quote" or "end quote" → "
- "comma" → ,
- "period" → .
- "question mark" → ?
- "exclamation mark" → !

Example: If speaker says "open quote hello close quote", transcribe as: "hello" """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-transcribe"):
        """Initialize the transcriber with API key and model."""
        if OpenAI is None:
            raise RuntimeError(
                "openai is not installed; install it to use transcription "
                "(e.g. `pip install openai`)"
            ) from _OPENAI_IMPORT_ERROR
        self.api_key = api_key or get_openai_api_key()
        
        self.client = OpenAI(api_key=self.api_key)
        self.model = model

    def _resolve_prompt(self, *, prompt: Optional[str], effective_model: str) -> Optional[str]:
        builtin: Optional[str] = None
        if effective_model.startswith("gpt-4"):
            builtin = self.GPT4_PROMPT
        elif effective_model == "whisper-1":
            builtin = self.WHISPER_PROMPT

        cleaned = (prompt or "").strip()
        if cleaned:
            if builtin:
                return f"{builtin}\n\n{cleaned}"
            return cleaned
        return builtin

    def transcribe_file(
        self,
        file: object,
        *,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        """Transcribe an uploaded audio file-like/bytes payload."""
        effective_model = (model or self.model or "").strip()
        if not effective_model:
            raise RuntimeError("No model specified for transcription")

        resolved_prompt = self._resolve_prompt(prompt=prompt, effective_model=effective_model)

        params = {
            "model": effective_model,
            "file": file,
            "response_format": "text",
            "temperature": float(temperature),
        }
        if language:
            params["language"] = language
        if resolved_prompt:
            params["prompt"] = resolved_prompt

        transcript = self.client.audio.transcriptions.create(**params)
        if isinstance(transcript, str):
            return transcript.strip()
        return str(transcript).strip()

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        """Transcribe audio bytes (WAV/MP3/etc) without writing to disk."""
        file_param = (filename, audio_bytes, content_type)
        return self.transcribe_file(
            file_param,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            model=model,
        )

    def transcribe_fileobj(
        self,
        fh: BinaryIO,
        *,
        filename: str = "audio.wav",
        content_type: str = "audio/wav",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        """Transcribe a file-like object without requiring a filesystem path."""
        file_param = (filename, fh, content_type)
        return self.transcribe_file(
            file_param,
            language=language,
            prompt=prompt,
            temperature=float(temperature),
            model=model,
        )
    
    def transcribe(
        self,
        audio_file: str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
        temperature: float = 0.0,
        model: Optional[str] = None,
    ) -> str:
        """Transcribe an audio file using Whisper API."""
        try:
            with open(audio_file, "rb") as f:
                return self.transcribe_file(
                    f,
                    language=language,
                    prompt=prompt,
                    temperature=float(temperature),
                    model=model,
                )
            
        except Exception as e:
            raise RuntimeError(f"Transcription failed: {e}")
