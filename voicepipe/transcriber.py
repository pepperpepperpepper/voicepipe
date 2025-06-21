"""OpenAI Whisper API integration for transcription."""

import os
import sys
from pathlib import Path
from typing import Optional

try:
    from openai import OpenAI
except ImportError:
    print("Error: openai package not installed. Install with: pip install openai", file=sys.stderr)
    sys.exit(1)


class WhisperTranscriber:
    """Handles transcription using OpenAI's Whisper API."""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize the transcriber with API key."""
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY')
        
        if not self.api_key:
            # Check common config locations
            config_paths = [
                Path.home() / '.config' / 'voicepipe' / 'api_key',
                Path.home() / '.voicepipe_api_key',
            ]
            
            for path in config_paths:
                if path.exists():
                    self.api_key = path.read_text().strip()
                    break
        
        if not self.api_key:
            raise ValueError(
                "OpenAI API key not found. Please set OPENAI_API_KEY environment variable "
                "or save your API key to ~/.config/voicepipe/api_key"
            )
        
        self.client = OpenAI(api_key=self.api_key)
    
    def transcribe(self, audio_file: str, language: Optional[str] = None) -> str:
        """Transcribe an audio file using Whisper API."""
        try:
            with open(audio_file, 'rb') as f:
                params = {
                    "model": "whisper-1",
                    "file": f,
                    "response_format": "text",
                }
                
                if language:
                    params["language"] = language
                
                transcript = self.client.audio.transcriptions.create(**params)
                
            return transcript.strip()
            
        except Exception as e:
            raise RuntimeError(f"Transcription failed: {e}")