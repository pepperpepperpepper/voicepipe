"""OpenAI Whisper API integration for transcription."""

import sys
from typing import Optional

from .config import get_openai_api_key

try:
    from openai import OpenAI
except ImportError:
    print("Error: openai package not installed. Install with: pip install openai", file=sys.stderr)
    sys.exit(1)


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
        self.api_key = api_key or get_openai_api_key()
        
        self.client = OpenAI(api_key=self.api_key)
        self.model = model
    
    def transcribe(self, audio_file: str, language: Optional[str] = None, prompt: Optional[str] = None, temperature: float = 0.0) -> str:
        """Transcribe an audio file using Whisper API."""
        try:
            with open(audio_file, 'rb') as f:
                params = {
                    "model": self.model,
                    "file": f,
                    "response_format": "text",
                    "temperature": temperature,
                }
                
                if language:
                    params["language"] = language
                
                # Use appropriate default prompt if none provided
                if prompt is None:
                    if self.model.startswith('gpt-4'):
                        prompt = self.GPT4_PROMPT
                    elif self.model == 'whisper-1':
                        prompt = self.WHISPER_PROMPT
                
                if prompt:
                    params["prompt"] = prompt
                
                transcript = self.client.audio.transcriptions.create(**params)
                
            return transcript.strip()
            
        except Exception as e:
            raise RuntimeError(f"Transcription failed: {e}")
