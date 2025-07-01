# Voicepipe

Cross-platform voice recording and transcription daemon.

## Features

- Simple voice recording with automatic (or manual) device detection
- High-quality transcription using OpenAI Whisper API
- Optional automatic typing of transcribed text via xdotool
- Robust daemon-based session management
- Automatic cleanup of temporary files
- Multi-language transcription support
- pip and pipx friendly installation

## Installation

**Cross-Platform Recommendation:**

For isolated installation, `pipx` is recommended on all platforms if you have it.
```bash
pipx install voicepipe
# Or, to include systray support:
pipx install "voicepipe[systray]"
# For Windows, pywin32 for named pipes will be installed if you use the install.ps1 script
# or install with poetry using the windows-support extra.
```

If you prefer `pip` in a virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install "voicepipe[systray]"
```

**Platform-Specific Instructions:**

### Windows

1.  **Clone the repository (if not done already):**
    ```bash
    git clone https://github.com/yourusername/voicepipe.git
    cd voicepipe
    ```
2.  **Run the installation script:**
    Open PowerShell and run:
    ```powershell
    .\install.ps1
    ```
    This script will:
    *   Check for Python and Poetry.
    *   Offer to install Poetry if it's missing.
    *   Install Voicepipe and its dependencies (including `pywin32` for IPC and `pystray` for the systray icon) using `poetry install --extras "systray windows-support"`.

3.  **Manual installation (if `install.ps1` fails or for custom setup):**
    *   Ensure Python 3.9+ and Poetry are installed.
    *   Install dependencies:
        ```bash
        poetry install --extras "systray windows-support"
        # "windows-support" includes pywin32, "systray" includes pystray and Pillow
        ```

**PyAudio on Windows:**
Voicepipe uses PyAudio for recording. On Windows, `pip` (and therefore Poetry) usually installs a pre-compiled version of PyAudio that includes the necessary PortAudio components. If you encounter audio input issues:
*   Ensure your microphone is properly configured in Windows sound settings.
*   Check that no other application is exclusively using the microphone.
*   For advanced scenarios (e.g., needing ASIO support, which is not common for this tool's purpose), you might need to compile PyAudio and PortAudio manually. Refer to PyAudio documentation.

### Linux

The `install.sh` script is provided for Linux users, especially if setting up the `systemd` user service is desired for performance and background operation.

```bash
git clone https://github.com/yourusername/voicepipe.git # If you haven't already
cd voicepipe
./install.sh
```
This script uses `pipx` to install Voicepipe with systray support and sets up a systemd user service.

**Systemd User Service (Linux):**
The `install.sh` script attempts to set this up. It provides:
- Better performance (no startup delay for the daemon).
- Automatic start on login (if enabled).
- Proper process management.

To manage the service:
```bash
systemctl --user enable voicepipe.service  # To enable auto-start
systemctl --user start voicepipe.service   # To start now
systemctl --user status voicepipe.service  # To check status
systemctl --user stop voicepipe.service    # To stop
```
The Voicepipe CLI automatically detects and communicates with the running service. If the service is not running, it may fall back to a subprocess mode (depending on final CLI implementation for non-service scenarios).

## Dependencies

- Python 3.9+
- **PyAudio**: For audio recording.
- **OpenAI Python SDK**: For transcription via Whisper API.
- **Click**: For the command-line interface.
- **python-dotenv**: For loading API keys from `.env` files.
- **pystray & Pillow** (optional, for systray icon): Installed with `[systray]` extra or by `install.sh`/`install.ps1`.
- **pywin32** (Windows only, for IPC): Installed by `install.ps1` or with `[windows-support]` extra.
- **xdotool** (Linux only, optional, for `--type` functionality):
    - On Arch Linux: `sudo pacman -S xdotool`
    - On Ubuntu/Debian: `sudo apt-get install xdotool`

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

Voicepipe commands are generally run from the command line. If you installed using Poetry in a project directory, you'll typically prefix commands with `poetry run`, for example, `poetry run voicepipe --help`. If installed with `pipx` or globally with `pip`, you can just use `voicepipe --help`.

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

### Running the Daemon (Background Process)

For features like the systray icon and instant recording start, the Voicepipe daemon should be running in the background.

**Linux:**
The `install.sh` script sets up a systemd user service (`voicepipe.service`) which is the recommended way to run the daemon.
- Start/Stop: `systemctl --user start/stop voicepipe.service`
- Enable/Disable auto-start: `systemctl --user enable/disable voicepipe.service`
- Check status: `systemctl --user status voicepipe.service`

If not using the service, you can run it manually (e.g., for debugging):
```bash
voicepipe daemon
```

**Windows:**
There are several ways to run the daemon on Windows:

1.  **Manual (for testing/debugging):**
    Open PowerShell or Command Prompt in the project directory:
    ```powershell
    poetry run voicepipe daemon
    ```
    A console window will remain open.

2.  **Simple Background Task (using `pythonw.exe`):**
    This runs the daemon without a console window, as the current user.
    *   Find your Poetry virtual environment path: `poetry env info --path`
    *   Construct the command: `<path-to-poetry-venv>\Scripts\pythonw.exe voicepipe\daemon.py`
        (Replace `<path-to-poetry-venv>` with the actual path from the previous step).
    *   To auto-start on login, create a shortcut to this command and place it in your Startup folder (`Win+R`, type `shell:startup`).

3.  **As a Windows Service (using NSSM - Recommended for robustness):**
    NSSM (Non-Sucking Service Manager) allows you to run Voicepipe as a proper Windows service (auto-starts with Windows, can restart on failure).
    *   Download NSSM from [https://nssm.cc/download](https://nssm.cc/download).
    *   Extract `nssm.exe` to a known location (e.g., `C:\NSSM`).
    *   Open an **Administrator** PowerShell or Command Prompt.
    *   Navigate to the NSSM directory: `cd C:\NSSM`
    *   Run: `.\nssm.exe install VoicepipeDaemon`
    *   In the NSSM GUI:
        *   **Application Tab:**
            *   **Path:** Select `python.exe` or `pythonw.exe` (for no console) from your Poetry virtual environment. (Find venv path with `poetry env info --path`, then look in its `Scripts` subfolder).
            *   **Startup directory:** The full path to your Voicepipe project directory.
            *   **Arguments:** `voicepipe\daemon.py`
            *   *(Alternative for Arguments if `poetry` is in PATH and you prefer to run via poetry)*:
                *   Path: `C:\path\to\your\poetry\bin\poetry.exe` (or wherever poetry.exe is)
                *   Startup directory: Voicepipe project directory
                *   Arguments: `run voicepipe daemon`
        *   **Details Tab:**
            *   Display name: `Voicepipe Recording Daemon` (or similar)
        *   **I/O Tab (Optional):** Configure paths for stdout/stderr logging if desired.
        *   **Restart Tab (Optional):** Configure auto-restart behavior.
    *   Click **Install service**.
    *   Start the service: `net start VoicepipeDaemon` or via the Services app (services.msc).
    *   To remove later: `.\nssm.exe remove VoicepipeDaemon` (as Administrator).

### Direct Typing (`--type`)

The `--type` option uses `xdotool` on Linux to simulate typing. This functionality is **currently not implemented for Windows or macOS**. Pull requests for cross-platform typing support (e.g., using `pyautogui` or platform-specific APIs) are welcome!

## Window Manager Integration (Linux)

This section details how to integrate Voicepipe with common Linux window managers for quick keyboard shortcuts.

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

**Linux:**
If `pip install pyaudio` (or `poetry install`) fails with errors related to missing PortAudio headers:
```bash
# Ubuntu/Debian
sudo apt-get install portaudio19-dev python3-dev

# Fedora/RHEL-based
sudo dnf install portaudio-devel python3-devel

# Arch Linux
sudo pacman -S portaudio python
```
Then try installing PyAudio again.

**macOS:**
```bash
brew install portaudio
pip install pyaudio
```

**Windows:**
As mentioned in the Windows Installation section, `pip` usually installs a pre-compiled PyAudio wheel with PortAudio included. If you face issues, ensure your Python environment is standard and your pip is up to date. If problems persist, consult PyAudio's documentation for Windows-specific troubleshooting.

### Permission Denied on /tmp (or equivalent temp directory)
Ensure your system's temporary directory (usually `/tmp` on Linux, or `%TEMP%` on Windows) has proper write permissions for your user. Voicepipe stores temporary audio files there.
```bash
ls -ld /tmp  # Should show: drwxrwxrwt
```

### Audio Device Not Found
List available devices and ensure your microphone is connected:
```bash
python -m pyaudio
```
# voicepipe
