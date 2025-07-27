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

- Python 3.8+
- PyAudio (for recording)
- OpenAI Python SDK
- Click (for CLI)
- xdotool (optional, for --type functionality)

On Arch Linux:
```bash
sudo pacman -S python-pyaudio xdotool
```

On Ubuntu/Debian:
```bash
sudo apt-get install python3-pyaudio xdotool
```

## Configuration

### API Key Setup

Voicepipe requires an OpenAI API key. Set it up using one of these methods:

1. **Environment variable** (recommended):
   ```bash
   export OPENAI_API_KEY="your-api-key-here"
   ```

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
python -c "import pyaudio; p=pyaudio.PyAudio(); print('\n'.join([f'{i}: {p.get_device_info_by_index(i)[\"name\"]}' for i in range(p.get_device_count()) if p.get_device_info_by_index(i)[\"maxInputChannels\"] > 0]))"

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

All errors are printed to stderr. When using `--type`, errors are also typed into the active window (useful for debugging in full-screen applications).

Common issues:
- **"No active recording session found"**: No recording is in progress
- **"Recording already in progress"**: A recording session is already active
- **"OpenAI API key not found"**: Set up your API key as described above
- **"xdotool not found"**: Install xdotool for --type functionality

## Technical Details

- Audio format: 16kHz, 16-bit, mono WAV (optimal for speech recognition)
- Temporary files: Stored in `/tmp/voicepipe-*.wav`
- State tracking: JSON files in `/tmp/voicepipe-{PID}.json`
- Automatic cleanup on process termination
- PID-based session management prevents conflicts

## License

MIT License - see LICENSE file for details

## Contributing

Contributions welcome! Please submit issues and pull requests on GitHub.

## Troubleshooting

### PyAudio Installation Issues
If you encounter issues installing PyAudio:
```bash
# Ubuntu/Debian
sudo apt-get install portaudio19-dev python3-dev

# macOS
brew install portaudio

# Then install PyAudio
pip install pyaudio
```

### Permission Denied on /tmp
Ensure your system's /tmp directory has proper permissions:
```bash
ls -ld /tmp  # Should show: drwxrwxrwt
```

### Audio Device Not Found
List available devices and ensure your microphone is connected:
```bash
python -m pyaudio
```
# voicepipe
