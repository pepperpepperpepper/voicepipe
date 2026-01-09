# Voicepipe

Voice recording and transcription CLI for Linux (systemd optional) and Windows 10/11 (daemonless).

## Features

- Simple voice recording with automatic device detection
- High-quality transcription using OpenAI or ElevenLabs (configurable)
- Optional automatic typing of transcribed text (Linux: xdotool/wtype; Windows: SendInput)
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
git clone https://github.com/pepperpepperpepper/voicepipe.git
cd voicepipe
./install.sh

# One-command setup (config + systemd services)
voicepipe setup
```

The systemd user services provide:
- Better performance (no startup delay)
- Automatic start on login
- Proper process management
- Lower resource usage
- Separate recorder and transcriber processes for better reliability

The CLI automatically detects and uses the services if running, or falls back to subprocess mode.

## Windows (Win10/11, no WSL)

Windows uses the subprocess recording path by default (no systemd, no recorder/transcriber daemons yet). The recommended hotkey target is `voicepipe-fast toggle` (records on first press, stops/transcribes on second press).

- Config file default: `%APPDATA%\\voicepipe\\voicepipe.env` (override with `VOICEPIPE_ENV_FILE`; run `voicepipe config show` to see the resolved path)
- Logs: `%LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log` (override with `VOICEPIPE_FAST_LOG_FILE` or `VOICEPIPE_LOG_FILE`)
- Typing: `--type` defaults to `sendinput` and requires an interactive desktop session; typing into elevated apps usually requires running Voicepipe elevated too
- Daemon policy: `VOICEPIPE_DAEMON_MODE=auto|never|always` (on Windows, `auto` behaves like `never`)

### Native hotkey runner (recommended)

Voicepipe includes a stdlib-only Windows hotkey runner that registers **Alt+F5** and triggers `voicepipe-fast toggle` in-process:

```powershell
# Run it (no console):
pythonw -m voicepipe.win_hotkey
```

Install it to start at login:

```powershell
voicepipe hotkey install
```

### From source (Windows)

```powershell
choco install -y git python312
git clone https://github.com/pepperpepperpepper/voicepipe.git
cd voicepipe
.\install.ps1 -Hotkey
```

If PowerShell blocks the script due to execution policy, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Hotkey
```

### AutoHotkey (optional)

```ahk
#Requires AutoHotkey v2.0
#SingleInstance Force

; Win+Shift+V toggles Voicepipe (runs hidden to avoid console flicker)
#+v::
{
    Run "voicepipe-fast toggle", , "Hide"
}
```

See `hotkey-examples/voicepipe.ahk` for a copy-pasteable file.

If `voicepipe-fast` is not on PATH, use `python -m voicepipe.fast toggle` instead (and ensure `python` is on PATH).

### Run at login (Windows)

- **Scheduled Task (recommended)**: `voicepipe hotkey install`
- **Startup folder shortcut**: `voicepipe hotkey install --method startup` (or add a shortcut that runs `pythonw -m voicepipe.win_hotkey`)
- Smoke test checklist: `WINDOWS_SMOKE_TEST.md`

## Dependencies

- Python 3.9–3.12
- sounddevice (for recording; requires PortAudio)
- OpenAI Python SDK (for the OpenAI backend)
- Click (for CLI)
- xdotool (optional, for `--type` on X11 / Xwayland; Linux only)
- wtype (optional, for `--type` on Wayland; Linux only)

On Arch Linux:
```bash
sudo pacman -S portaudio xdotool wtype
```

On Ubuntu/Debian:
```bash
sudo apt-get install portaudio19-dev xdotool wtype
```

## Configuration

### Transcription Backend

Voicepipe supports multiple transcription backends:

- `openai` (default): models like `gpt-4o-transcribe`, `whisper-1`
- `elevenlabs`: models like `scribe_v1`

Configure in your `voicepipe.env` file:
```bash
VOICEPIPE_TRANSCRIBE_BACKEND=openai
VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe
```

Note: the default `voicepipe.env` location is OS-dependent (Linux: `~/.config/voicepipe/voicepipe.env`; Windows: `%APPDATA%\\voicepipe\\voicepipe.env`). Run `voicepipe config show` to see the resolved path.

You can also override per command by prefixing the model:
```bash
voicepipe stop --model openai:whisper-1
voicepipe stop --model elevenlabs:scribe_v1
```

### API Key Setup

Voicepipe requires an API key for the selected backend:

- `openai`: `OPENAI_API_KEY`
- `elevenlabs`: `ELEVENLABS_API_KEY` (or `XI_API_KEY`)

Set it up using one of these methods:

0. **One-command setup (recommended)**:
   ```bash
   voicepipe setup
   # Or:
   voicepipe setup --backend elevenlabs
   ```

1. **Env file (recommended; works for systemd services + CLI)**:
   On Linux/macOS:
   ```bash
   mkdir -p ~/.config/voicepipe
   chmod 700 ~/.config/voicepipe
   echo 'VOICEPIPE_TRANSCRIBE_BACKEND=openai' >> ~/.config/voicepipe/voicepipe.env
   echo 'VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe' >> ~/.config/voicepipe/voicepipe.env
   echo 'OPENAI_API_KEY=your-api-key-here' >> ~/.config/voicepipe/voicepipe.env
   chmod 600 ~/.config/voicepipe/voicepipe.env
   ```
   On Windows, edit `%APPDATA%\\voicepipe\\voicepipe.env` (or run `voicepipe config show` to find it).
   Or use the CLI (avoids shell history):
   ```bash
   echo 'your-api-key-here' | voicepipe config set-openai-key --from-stdin
   # For ElevenLabs:
   echo 'your-api-key-here' | voicepipe config set-elevenlabs-key --from-stdin
   ```
   If you use the systemd services, restart Voicepipe after changes:
   ```bash
   voicepipe service restart
   # or:
   systemctl --user restart voicepipe.target
   ```

2. **Environment variable** (works for interactive shells; systemd services won’t see `.bashrc` exports):
   ```bash
   export OPENAI_API_KEY="your-api-key-here"
   # ElevenLabs:
   export ELEVENLABS_API_KEY="your-api-key-here"
   ```
   Voicepipe also loads a `.env` file (if present) on startup.

3. **Legacy config file**:
   ```bash
   mkdir -p ~/.config/voicepipe
   echo "your-api-key-here" > ~/.config/voicepipe/api_key
   ```

4. **Legacy alternative location**:
   ```bash
   echo "your-api-key-here" > ~/.voicepipe_api_key
   ```

### Advanced: systemd credentials (optional)

Voicepipe can also read the key from systemd credentials (via `$CREDENTIALS_DIRECTORY`) if you configure `LoadCredential=` for the transcriber service.

### Typing (X11 vs Wayland)

`--type` uses an external tool to type into the currently focused application:

- X11 / Xwayland: `xdotool`
- Wayland: `wtype` (the Wayland analogue of `xdotool`, using a virtual keyboard protocol)
- Windows: `sendinput` (built-in; no external binary)

By default, Voicepipe auto-selects a backend. You can override it in your `voicepipe.env` file:

```bash
VOICEPIPE_TYPE_BACKEND=auto  # or: wayland|x11|wtype|xdotool|sendinput|none
```

Note: On Wayland, `wtype` cannot target a specific window ID the way `xdotool --window` can; typing is best-effort into the focused surface.

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

#### One-Command Dictation
Record, stop, transcribe, and optionally type in a single command:
```bash
# Record until you press ENTER, then transcribe:
voicepipe dictate

# Record for a fixed duration, then transcribe and type:
voicepipe dictate --seconds 5 --type
```

#### Transcribe an Existing Audio File
```bash
voicepipe transcribe-file path/to/audio.mp3
voicepipe transcribe-file path/to/audio.mp3 --type
```

#### Smoke Test
Run an end-to-end file transcription against a known sample:
```bash
voicepipe smoke

# Or test a specific file and expected phrase:
voicepipe smoke test.mp3 --expected "ask not what your country can do for you"
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
Voicepipe supports multiple backends and models:

- OpenAI (`VOICEPIPE_TRANSCRIBE_BACKEND=openai`):
  - `gpt-4o-transcribe`: best quality (typically higher cost)
  - `gpt-4o-mini-transcribe`: faster/cheaper (typically lower quality)
  - `whisper-1`: legacy Whisper model
- ElevenLabs (`VOICEPIPE_TRANSCRIBE_BACKEND=elevenlabs`):
  - `scribe_v1`: default
  - `scribe_v1_experimental`: experimental

```bash
voicepipe start
voicepipe stop --model gpt-4o-transcribe
voicepipe stop --model gpt-4o-mini-transcribe
voicepipe stop --model whisper-1
voicepipe stop --model elevenlabs:scribe_v1
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
# One-key toggle (recommended)
# Starts recording if idle; stops, transcribes, and types if recording.
bindsym $mod+r exec voicepipe-toggle

# Manual start/stop (separate keys)
# Start recording
bindsym $mod+Shift+r exec voicepipe start

# Stop and transcribe
bindsym $mod+Control+r exec voicepipe stop

# Stop and type
bindsym $mod+Mod1+r exec voicepipe stop --type

# Cancel recording
bindsym $mod+Escape exec voicepipe cancel
```

### GNOME (using custom shortcuts)
1. Open Settings → Keyboard → Keyboard Shortcuts
2. Add custom shortcuts:
   - Name: "Toggle Voice Recording"
   - Command: `voicepipe-toggle`
   - Shortcut: Super+R

### KDE Plasma
1. System Settings → Shortcuts → Custom Shortcuts
2. Edit → New → Global Shortcut → Command/URL
3. Set trigger and action to voicepipe commands

### Awesome WM
```lua
awful.key({ modkey }, "r", function() awful.spawn("voicepipe-toggle") end),
```

## Error Handling

All errors are printed to stderr.

Common issues:
- **"No active recording session found"**: No recording is in progress
- **"Recording already in progress"**: A recording session is already active
- **"OpenAI API key not found"**: Configure `OPENAI_API_KEY` (backend=`openai`)
- **"ElevenLabs API key not found"**: Configure `ELEVENLABS_API_KEY` (backend=`elevenlabs`)
- **Typing backend missing**: install `xdotool` (X11) or `wtype` (Wayland), or set `VOICEPIPE_TYPE_BACKEND=none` to disable typing.

Diagnostics:
- `voicepipe doctor`
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

## Testing

Offline/unit tests (no mic/systemd/OpenAI required):
```bash
pytest -q
```

Opt-in live integration tests (network + API key; may use your microphone):
```bash
VOICEPIPE_LIVE_TESTS=1 pytest -q tests/test_live_integration.py
```

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
