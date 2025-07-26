#!/home/pepper/.local/share/pipx/venvs/voicepipe/bin/python
"""Persistent transcriber daemon with pre-initialized OpenAI client"""
import os
import sys
import socket
import json
import signal
import time
from pathlib import Path

# Pre-initialize the transcriber
print("Initializing transcriber...", file=sys.stderr)
from voicepipe.transcriber import WhisperTranscriber
transcriber = WhisperTranscriber(model='gpt-4o-transcribe')
print("Transcriber ready", file=sys.stderr)

SOCKET_PATH = Path('/tmp/voicepipe_transcriber.sock')
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
        data = conn.recv(4096).decode()
        if not data:
            conn.close()
            continue
            
        request = json.loads(data)
        audio_file = request.get('audio_file')
        
        if audio_file and os.path.exists(audio_file):
            start_time = time.time()
            text = transcriber.transcribe(audio_file)
            transcribe_time = time.time() - start_time
            
            response = {
                'text': text,
                'time': transcribe_time
            }
        else:
            response = {'error': 'Audio file not found'}
            
        conn.send(json.dumps(response).encode())
        conn.close()
        
    except socket.timeout:
        continue
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)

server.close()
if SOCKET_PATH.exists():
    SOCKET_PATH.unlink()