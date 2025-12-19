# Voicepipe

Voice recording and transcription daemon for Linux.

## Features

- Simple voice recording with automatic device detection
- High-quality transcription using OpenAI Whisper API
- Optional automatic typing of transcribed text via xdotool
- Robust daemon-based session management
- Automatic cleanup of temporary files
- Multi-language transcription support
- pip and pipx friendly installation

## Installation

### Using pip
```bash
pip install voicepipe
```

### Using pipx (recommended)
```bash
pipx install voicepipe
```

### From source with systemd services (recommended for performance)
```bash
git clone https://github.com/yourusername/voicepipe.git
cd voicepipe
./install.sh

# Enable and start both services
systemctl --user enable voicepipe-recorder.service voicepipe-transcriber.service
systemctl --user start voicepipe-recorder.service voicepipe-transcriber.service
```

The systemd user services provide:
- Better performance (no startup delay)
- Automatic start on login
- Proper process management
- Lower resource usage
- Separate recorder and transcriber processes for better reliability

The CLI automatically detects and uses the services if running, or falls back to subprocess mode.

## Dependencies

- Python 3.9+
- sounddevice (for recording; requires PortAudio)
- OpenAI Python SDK
- Click (for CLI)
- xdotool (optional, for --type functionality)

On Arch Linux:
```bash
sudo pacman -S portaudio xdotool
```

On Ubuntu/Debian:
```bash
sudo apt-get install portaudio19-dev xdotool
```

## Configuration

### API Key Setup

Voicepipe requires an OpenAI API key. Set it up using one of these methods:

1. **Environment variable** (recommended):
   ```bash
   export OPENAI_API_KEY="your-api-key-here"
   ```
   Voicepipe also loads a `.env` file (if present) on startup.

2. **Config file**:
   ```bash
   mkdir -p ~/.config/voicepipe
   echo "your-api-key-here" > ~/.config/voicepipe/api_key
   ```

3. **Alternative config location**:
   ```bash
   echo "your-api-key-here" > ~/.voicepipe_api_key
   ```

### Audio Device Configuration

By default, voicepipe uses the system's default audio input device. To use a specific device:

```bash
# List available devices
python -c "import sounddevice as sd; print('\\n'.join([f\"{i}: {d['name']}\" for i, d in enumerate(sd.query_devices()) if d.get('max_input_channels', 0) > 0]))"

# Set device by index
export VOICEPIPE_DEVICE=2
# Or use --device flag
voicepipe start --device 2
```

## Usage

### Basic Recording and Transcription

1. **Start recording**:
   ```bash
   voicepipe start
   ```

2. **Stop and transcribe**:
   ```bash
   voicepipe stop
   ```
   This outputs the transcribed text to stdout.

3. **Cancel recording** (no transcription):
   ```bash
   voicepipe cancel
   ```

### Examples

#### Terminal Output
Record and display transcription in terminal:
```bash
voicepipe start
# Speak into microphone...
voicepipe stop
# Output: "Hello, this is my transcribed text."
```

#### Direct Typing
Record and type directly into active window:
```bash
voicepipe start
# Speak into microphone...
voicepipe stop --type
# Text is typed into current application
```

#### Save to Clipboard
```bash
voicepipe start
# Speak...
voicepipe stop | xclip -selection clipboard
# Or on Wayland:
voicepipe stop | wl-copy
```

#### Language-Specific Transcription
```bash
voicepipe start
voicepipe stop --language es  # Spanish
voicepipe stop --language fr  # French
voicepipe stop --language de  # German
```

#### Model Selection
Voicepipe supports multiple transcription models:
- `gpt-4o-transcribe`: best quality (typically higher cost)
- `gpt-4o-mini-transcribe`: faster/cheaper (typically lower quality)
- `whisper-1`: legacy Whisper model

```bash
voicepipe start
voicepipe stop --model gpt-4o-transcribe
voicepipe stop --model gpt-4o-mini-transcribe
voicepipe stop --model whisper-1
```

#### Shell Scripting
```bash
# Save to file
voicepipe start && voicepipe stop > note.txt

# Append to file
voicepipe start && voicepipe stop >> notes.txt

# Use in command substitution
echo "Note: $(voicepipe start && voicepipe stop)"

# Pipe to other commands
voicepipe start && voicepipe stop | tr '[:lower:]' '[:upper:]'
```

## Window Manager Integration

### i3/Sway
Add to your config:
```
# Start recording
bindsym $mod+r exec voicepipe start

# Stop and transcribe
bindsym $mod+Shift+r exec voicepipe stop

# Stop and type
bindsym $mod+Control+r exec voicepipe stop --type

# Cancel recording
bindsym $mod+Escape exec voicepipe cancel
```

### GNOME (using custom shortcuts)
1. Open Settings → Keyboard → Keyboard Shortcuts
2. Add custom shortcuts:
   - Name: "Start Voice Recording"
   - Command: `voicepipe start`
   - Shortcut: Super+R

### KDE Plasma
1. System Settings → Shortcuts → Custom Shortcuts
2. Edit → New → Global Shortcut → Command/URL
3. Set trigger and action to voicepipe commands

### Awesome WM
```lua
awful.key({ modkey }, "r", function() awful.spawn("voicepipe start") end),
awful.key({ modkey, "Shift" }, "r", function() awful.spawn("voicepipe stop --type") end),
```

## Error Handling

All errors are printed to stderr.

Common issues:
- **"No active recording session found"**: No recording is in progress
- **"Recording already in progress"**: A recording session is already active
- **"OpenAI API key not found"**: Set up your API key as described above
- **"xdotool not found"**: Install xdotool for --type functionality

Diagnostics:
- `voicepipe doctor env`
- `voicepipe doctor daemon --record-test --record-seconds 2 --play`
- `voicepipe doctor audio`

## Technical Details

- Audio format: 16kHz, 16-bit, mono WAV (optimal for speech recognition)
- Runtime files (sockets, temp audio, session JSON): stored in `$XDG_RUNTIME_DIR/voicepipe` (or `/tmp/voicepipe-$UID` fallback)
- Recorder daemon socket: `.../voicepipe.sock`
- Transcriber daemon socket: `.../voicepipe_transcriber.sock`
- Temporary audio files: `voicepipe_*.wav` in the runtime dir
- State tracking (subprocess mode): `voicepipe-{PID}.json` in the runtime dir
- Preserved audio on transcription failure: `~/.local/state/voicepipe/audio` (or `$XDG_STATE_HOME/voicepipe/audio`)
- Automatic cleanup on process termination
- PID-based session management prevents conflicts

## License

MIT License - see LICENSE file for details

## Contributing

Contributions welcome! Please submit issues and pull requests on GitHub.

## Troubleshooting

### sounddevice / PortAudio Issues
If you encounter issues with audio recording, ensure PortAudio is installed and that your user can access the microphone.

```bash
# Ubuntu/Debian
sudo apt-get install portaudio19-dev python3-dev

# macOS
brew install portaudio

# Then install Python deps
pip install sounddevice
```

### Permission Denied on /tmp
Ensure your system's /tmp directory has proper permissions:
```bash
ls -ld /tmp  # Should show: drwxrwxrwt
```

### Audio Device Not Found
List available devices and ensure your microphone is connected:
```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```
