#!/usr/bin/env python3
"""Persistent transcriber daemon with pre-initialized OpenAI client"""
import os
import sys
import socket
import json
import signal
import time
import tempfile
from pathlib import Path

# Load user environment variables before initializing transcriber
import subprocess
import os

# Try to source user environment from common locations
env_files = [
    os.path.expanduser('~/.api-keys'),
    os.path.expanduser('~/.bashrc'),
    os.path.expanduser('~/.bash_profile'),
    os.path.expanduser('~/.profile')
]

for env_file in env_files:
    if os.path.exists(env_file):
        try:
            # Source the environment file and get the updated environment
            result = subprocess.run(
                ['bash', '-c', f'source {env_file} && env'], 
                capture_output=True, 
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        if key == 'OPENAI_API_KEY':
                            os.environ[key] = value
                            break
        except:
            pass

# Pre-initialize the transcriber
print("Initializing transcriber...", file=sys.stderr)
from voicepipe.transcriber import WhisperTranscriber
transcriber = WhisperTranscriber(model='gpt-4o-transcribe')
print("Transcriber ready", file=sys.stderr)

SOCKET_PATH = Path('/tmp/voicepipe/voicepipe_transcriber.sock')
running = True

def signal_handler(signum, frame):
    global running
    running = False
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# Create socket
if SOCKET_PATH.exists():
    SOCKET_PATH.unlink()

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(str(SOCKET_PATH))
server.listen(1)
server.settimeout(0.5)  # Non-blocking with timeout

print(f"Transcriber daemon listening on {SOCKET_PATH}", file=sys.stderr)

while running:
    try:
        conn, _ = server.accept()
        conn.settimeout(300)  # 5 minutes for large file processing
        buffer = ''
        data = ''
        while True:
            try:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode('utf-8')
                if '\n' in buffer:
                    data, buffer = buffer.split('\n', 1)
                    break
            except socket.timeout:
                continue
        if not data:
            conn.close()
            continue
            
        try:
            request = json.loads(data)
        except json.JSONDecodeError as e:
            print(f"JSON error: {e}", file=sys.stderr)
            conn.send((json.dumps({'type': 'error', 'message': 'Invalid JSON'}) + '\n').encode())
            conn.close()
            continue
        
        # Handle both file path and hex audio data formats
        audio_file = request.get('audio_file')
        audio_hex = request.get('audio')
        
        if audio_hex:
            # Handle hex audio data from client
            audio_data = bytes.fromhex(audio_hex)
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False, dir='/tmp/voicepipe') as tmp_file:
                tmp_file.write(audio_data)
                tmp_path = tmp_file.name
            
            try:
                start_time = time.time()
                text = transcriber.transcribe(tmp_path)
                transcribe_time = time.time() - start_time
                
                response = {
                    'type': 'transcription',
                    'text': text,
                    'time': transcribe_time
                }
            finally:
                os.unlink(tmp_path)
                
        elif audio_file and os.path.exists(audio_file):
            start_time = time.time()
            text = transcriber.transcribe(audio_file)
            transcribe_time = time.time() - start_time
            
            response = {
                'type': 'transcription',
                'text': text,
                'time': transcribe_time
            }
        else:
            response = {'type': 'error', 'message': 'Audio file not found'}
            
        # Send streaming response format expected by client
        if response.get('type') == 'transcription':
            # Send transcription line by line for streaming
            text = response['text']
            lines = text.split('\n')
            for line in lines:
                if line.strip():
                    chunk_response = {'type': 'transcription', 'text': line + '\n'}
                    conn.send((json.dumps(chunk_response) + '\n').encode())
            
            # Send completion signal
            conn.send((json.dumps({'type': 'complete'}) + '\n').encode())
        else:
            conn.send((json.dumps(response) + '\n').encode())
        
        conn.close()
        
    except socket.timeout:
        continue
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

server.close()
if SOCKET_PATH.exists():
    SOCKET_PATH.unlink()