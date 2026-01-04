"""OpenAI Whisper API integration for transcription."""

from __future__ import annotations

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
            effective_model = (model or self.model or "").strip()
            if not effective_model:
                raise RuntimeError("No model specified for transcription")

            with open(audio_file, 'rb') as f:
                params = {
                    "model": effective_model,
                    "file": f,
                    "response_format": "text",
                    "temperature": temperature,
                }
                
                if language:
                    params["language"] = language
                
                # Use appropriate default prompt if none provided
                if prompt is None:
                    if effective_model.startswith('gpt-4'):
                        prompt = self.GPT4_PROMPT
                    elif effective_model == 'whisper-1':
                        prompt = self.WHISPER_PROMPT
                
                if prompt:
                    params["prompt"] = prompt
                
                transcript = self.client.audio.transcriptions.create(**params)
                
            return transcript.strip()
            
        except Exception as e:
            raise RuntimeError(f"Transcription failed: {e}")
