# Voicepipe Windows Setup Documentation

**This document describes the exact working setup on this Windows machine with WSL.**

## Overview

This setup runs Voicepipe as a background service on Windows with:
- **Voice recording daemon** running via Python in the background
- **System tray icon** (optional, but can be added)
- **Global hotkey** (Alt+F5) to toggle recording via AutoHotkey v2
- **Automatic startup** on Windows login via VBScript launcher in Startup folder
- **Automatic transcription and typing** of recorded text into active window

## Current Working Configuration

### System Information
- **OS**: Windows with WSL (Ubuntu/Debian based)
- **Python**: 3.12 (via Poetry virtual environment)
- **Poetry**: Installed in system path (`/usr/sbin/poetry`)
- **Poetry Virtual Environment Path**: 
  - Windows: `C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12`
  - WSL: Not configured (runs on Windows side only)

### Architecture

```
Windows Startup
    ↓
VBScript Launcher (in Startup folder)
    ↓
    ├─→ Python Daemon (via pythonw.exe - hidden)
    │   └─→ Listens on named pipe: \\.\pipe\voicepipe_daemon
    │
    └─→ AutoHotkey v2 Script (hidden)
        └─→ Listens for Alt+F5 hotkey
            └─→ Sends commands via Python → Named Pipe → Daemon
```

## File Locations

### 1. Core Repository
**Location**: `C:\Tools\voicepipe\` (also accessible via WSL at `/mnt/c/Tools/voicepipe/`)

This contains:
- Main voicepipe Python package (`voicepipe/` directory)
- Installation scripts (`install.ps1`, `install.sh`, etc.)
- Configuration files (`pyproject.toml`, `.env.example`)
- Documentation (`README.md`)

### 2. Working Directory (User Documents)
**Location**: `C:\Users\fenlo\Documents\voicepipe\`

This directory contains the **actual working scripts** that are currently running:

#### VBScript Files
- **`voicepipe_startup_truly_hidden.vbs`** ⭐ **CURRENTLY USED**
  - Main startup script that launches everything
  - Started automatically via shortcut in Startup folder
  - Launches daemon and hotkey script completely hidden
  
- `voicepipe_startup.vbs` - Earlier version
- `voicepipe_startup_fixed.vbs` - Earlier version
- `start_hotkey_truly_hidden.vbs` - Launched by main startup script to start AHK
- Various other helper VBS files for testing

#### AutoHotkey Files
- **`direct_toggle_stateful.ahk`** ⭐ **CURRENTLY USED**
  - AutoHotkey v2 script
  - Binds Alt+F5 to toggle recording
  - Includes debouncing (500ms cooldown)
  - Calls `fast_control.py` to communicate with daemon
  
- `direct_toggle_hidden.ahk` - Hidden window version
- Various other AHK files for testing/development

#### Python Helper Scripts
- **`fast_control.py`** ⭐ **CURRENTLY USED**
  - Fast control script that communicates with daemon via Windows named pipes
  - Supports commands: `start`, `stop`, `toggle`, `status`
  - Handles transcription and automatic typing via pyautogui
  - Logs debug info to `%TEMP%\voicepipe_debug.log`

### 3. Windows Startup Folder
**Location**: `C:\Users\fenlo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\`

Contains:
- **`Voicepipe.lnk`** - Shortcut that points to `voicepipe_startup_truly_hidden.vbs`

### 4. Poetry Virtual Environment
**Location**: `C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\`

Key files:
- `Scripts\pythonw.exe` - Python executable that runs without console window
- `Scripts\voicepipe` - Voicepipe CLI entry point
- All installed dependencies (pyaudio, openai, pywin32, pystray, pyautogui, etc.)

## Startup Sequence

When Windows starts:

1. **Windows Startup Folder** executes `Voicepipe.lnk`
2. **`Voicepipe.lnk`** launches `voicepipe_startup_truly_hidden.vbs`
3. **VBScript** waits 3 seconds for Windows to fully load
4. **VBScript** starts the daemon:
   ```
   C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\Scripts\pythonw.exe 
   C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12\Scripts\voicepipe daemon
   ```
   - Runs completely hidden (no console window)
   - Creates named pipe: `\\.\pipe\voicepipe_daemon`
   - Loads OPENAI_API_KEY from environment variables
   
5. **VBScript** waits 3 seconds for daemon initialization
6. **VBScript** launches `start_hotkey_truly_hidden.vbs`
7. **Hotkey VBScript** starts AutoHotkey: `direct_toggle_hidden.ahk`
   - Runs completely hidden
   - Registers Alt+F5 hotkey globally
   
8. System is ready! User can press **Alt+F5** to toggle recording

## Hotkey Workflow

When user presses **Alt+F5**:

1. **AutoHotkey** detects keypress
2. **AutoHotkey** calls:
   ```
   pythonw.exe fast_control.py toggle
   ```
3. **`fast_control.py`**:
   - Connects to named pipe `\\.\pipe\voicepipe_daemon`
   - Sends `status` command to check current state
   - If **not recording**: Sends `start` command
   - If **recording**: 
     - Sends `stop` command
     - Receives audio file path
     - Transcribes audio using OpenAI Whisper API
     - Types transcribed text into active window using `pyautogui`
4. All operations logged to `%TEMP%\voicepipe_debug.log`

## Key Features

### 1. Completely Hidden Operation
- No console windows appear
- Uses `pythonw.exe` instead of `python.exe`
- VBScript and AHK run with window style 0 (hidden)
- Only visual feedback is typed text appearing

### 2. Debouncing
- 500ms cooldown between hotkey presses
- Prevents accidental double-triggers
- Implemented in AutoHotkey script

### 3. Stateful Operation
- Checks actual daemon status before toggling
- Won't start if already recording
- Won't stop if not recording

### 4. Fast Communication
- Uses Windows named pipes (very fast IPC)
- Minimal overhead for hotkey response
- No subprocess creation for status checks

### 5. Debug Logging
- All operations logged to `%TEMP%\voicepipe_debug.log`
- Includes transcription results, errors, file paths
- Useful for troubleshooting

## Dependencies

### Python Packages (in Poetry venv)
From `pyproject.toml`:
```toml
click = "^8.0"
openai = "^1.0"
httpx = "^0.24"
python-dotenv = "^1.0"
pyaudio = "^0.2.11"
pystray = "^0.19.0"  # [systray] extra
pillow = "^10.0"     # [systray] extra
pywin32 = ">=300"    # [windows-support] extra - REQUIRED for named pipes
pyautogui = "^0.9.50"  # [typing] extra - REQUIRED for typing
```

### External Software
- **AutoHotkey v2** - Must be installed system-wide
  - Download from: https://www.autohotkey.com/
  - v2 syntax is different from v1!
- **Python 3.9+** - For Poetry virtual environment
- **Poetry** - For dependency management

## Environment Variables

The daemon requires:
- **`OPENAI_API_KEY`** - Your OpenAI API key for Whisper transcription
  - Can be set in System environment variables
  - Or User environment variables
  - VBScript automatically passes it to daemon process

## Installation Steps (To Replicate This Setup)

### 1. Install Prerequisites
```powershell
# Install Python 3.9+ from python.org
# Install Poetry
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | python -

# Install AutoHotkey v2
# Download from https://www.autohotkey.com/ and run installer
```

### 2. Clone and Set Up Voicepipe
```powershell
# Clone repository
cd C:\Tools
git clone https://github.com/pepperpepperpepper/voicepipe.git
cd voicepipe

# Install with Poetry
poetry install --extras "systray windows-support typing"
```

### 3. Create Working Directory
```powershell
# Create directory for runtime scripts
mkdir C:\Users\$env:USERNAME\Documents\voicepipe
cd C:\Users\$env:USERNAME\Documents\voicepipe
```

### 4. Copy Working Scripts

Copy these files from this repository backup to your working directory:

**From this documentation**, create:

#### `voicepipe_startup_truly_hidden.vbs`:
```vbscript
' Voicepipe Startup Script - Runs daemon and hotkeys completely hidden
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Wait a moment for Windows to fully start
WScript.Sleep 3000

' Start daemon with pythonw (no console window)
pythonwPath = "C:\Users\YOURUSERNAME\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-XXXXX-py3.XX\Scripts\pythonw.exe"
voicepipeScript = "C:\Users\YOURUSERNAME\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-XXXXX-py3.XX\Scripts\voicepipe"
daemonCmd = """" & pythonwPath & """ """ & voicepipeScript & """ daemon"

' Get OPENAI_API_KEY from system environment
Dim sysEnv
Set sysEnv = objShell.Environment("SYSTEM")
apiKey = sysEnv("OPENAI_API_KEY")

' If not in system env, try user env
If apiKey = "" Then
    Set userEnv = objShell.Environment("USER")
    apiKey = userEnv("OPENAI_API_KEY")
End If

' Pass the API key to the daemon process if found
If apiKey <> "" Then
    objShell.Environment("PROCESS")("OPENAI_API_KEY") = apiKey
End If

' Run daemon hidden
objShell.Run daemonCmd, 0, False

' Wait for daemon to initialize
WScript.Sleep 3000

' Start hotkeys using the truly hidden version
objShell.CurrentDirectory = "C:\Users\YOURUSERNAME\Documents\voicepipe"
objShell.Run "wscript start_hotkey_truly_hidden.vbs", 0, False
```

#### `start_hotkey_truly_hidden.vbs`:
```vbscript
' Start AutoHotkey script completely hidden
Set objShell = CreateObject("WScript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")

' Change to the voicepipe directory
objShell.CurrentDirectory = "C:\Users\YOURUSERNAME\Documents\voicepipe"

' Run the AutoHotkey script with window style 0 (hidden)
objShell.Run "direct_toggle_stateful.ahk", 0, False
```

#### `direct_toggle_stateful.ahk`:
```autohotkey
; Direct Voicepipe Toggle - AutoHotkey v2
; Stateful version that checks actual daemon status

global lastToggleTime := 0

; Alt+F5 = Toggle Recording (stateful with debouncing)
!F5::
{
    global lastToggleTime
    
    ; Get current time in milliseconds
    currentTime := A_TickCount
    
    ; Ignore if triggered within 500ms of last toggle
    if (currentTime - lastToggleTime < 500) {
        return
    }
    
    lastToggleTime := currentTime
    
    pythonwPath := "C:\Users\YOURUSERNAME\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-XXXXX-py3.XX\Scripts\pythonw.exe"
    controlScript := "C:\Users\YOURUSERNAME\Documents\voicepipe\fast_control.py"
    
    ; Always toggle based on current daemon state
    Run '"' . pythonwPath . '" "' . controlScript . '" toggle', , "Hide"
}

; Emergency stop - Alt+F12
!F12::
{
    Run 'powershell.exe -Command "Get-Process python*,ffmpeg* | Stop-Process -Force"', , "Hide"
}
```

#### `fast_control.py`:
See the full content in the earlier tool output - it's about 150 lines.

### 5. Update Paths in Scripts

**IMPORTANT**: Replace `YOURUSERNAME` and `XXXXX-py3.XX` in all scripts with actual values:

```powershell
# Get your Poetry venv path
poetry env info --path

# Example output:
# C:\Users\fenlo\AppData\Local\pypoetry\Cache\virtualenvs\voicepipe-8JzhALkX-py3.12

# Use this path in all VBScript and AHK files
```

### 6. Set Environment Variable
```powershell
# Set OPENAI_API_KEY (User level)
[System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "your-api-key-here", "User")

# Or System level (requires admin)
[System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "your-api-key-here", "Machine")
```

### 7. Create Startup Shortcut
```powershell
# Open Startup folder
explorer shell:startup

# Right-click → New → Shortcut
# Target: C:\Users\YOURUSERNAME\Documents\voicepipe\voicepipe_startup_truly_hidden.vbs
# Name it: Voicepipe
```

### 8. Test
```powershell
# Manually run the startup script to test
wscript C:\Users\$env:USERNAME\Documents\voicepipe\voicepipe_startup_truly_hidden.vbs

# Wait 5-10 seconds for everything to start

# Check if daemon is running
tasklist | findstr pythonw

# Test hotkey - Press Alt+F5, speak, press Alt+F5 again

# Check debug log
notepad $env:TEMP\voicepipe_debug.log
```

## Troubleshooting

### Check if Daemon is Running
```powershell
tasklist | findstr pythonw
# Should show pythonw.exe processes
```

### Check if AutoHotkey is Running
```powershell
tasklist | findstr AutoHotkey
# Should show AutoHotkeyU64.exe or similar
```

### View Debug Logs
```powershell
notepad $env:TEMP\voicepipe_debug.log
```

### Manually Test Daemon
```powershell
cd C:\Tools\voicepipe
poetry run voicepipe daemon
# Should start daemon in foreground with logging
```

### Kill Everything and Restart
```powershell
# Kill all voicepipe processes
tasklist | findstr "pythonw"
taskkill /F /IM pythonw.exe
taskkill /F /IM AutoHotkeyU64.exe

# Restart
wscript C:\Users\$env:USERNAME\Documents\voicepipe\voicepipe_startup_truly_hidden.vbs
```

### Test Named Pipe Communication
```python
# Test if daemon is listening
import win32pipe, win32file, json

pipe = win32file.CreateFile(
    r'\\.\pipe\voicepipe_daemon',
    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
    0, None, win32file.OPEN_EXISTING, 0, None
)

win32file.WriteFile(pipe, json.dumps({"command": "status"}).encode())
hr, response = win32file.ReadFile(pipe, 4096)
print(json.loads(response.decode()))
win32file.CloseHandle(pipe)
```

## Known Issues and Limitations

1. **Poetry venv path changes** - If you reinstall or update, the venv path hash (`8JzhALkX`) might change
2. **AutoHotkey v1 vs v2** - Syntax is incompatible, must use v2
3. **First transcription is slow** - OpenAI API takes 2-5 seconds
4. **No visual feedback** - Only typed text confirms operation
5. **Hotkey conflicts** - Alt+F5 might conflict with other apps
6. **API costs** - Each transcription costs ~$0.006 per minute

## Improvements for Future

1. **Add system tray icon** - Visual feedback for recording status
2. **Configurable hotkey** - Allow user to change from Alt+F5
3. **Visual notification** - Toast notification on start/stop
4. **Better error handling** - Show errors as notifications, not just logs
5. **Easier installation** - Automated PowerShell script to set everything up
6. **Self-updating paths** - Detect Poetry venv path automatically

## Files to Backup Before Wiping Drive

### Essential Working Files (C:\Users\fenlo\Documents\voicepipe\)
- ✅ `voicepipe_startup_truly_hidden.vbs`
- ✅ `start_hotkey_truly_hidden.vbs`
- ✅ `direct_toggle_stateful.ahk`
- ✅ `fast_control.py`

### Repository (C:\Tools\voicepipe\)
- ✅ Already backed up in Git (branch: `backup/windows-compatibility-20251001-221315`)

### Configuration
- ✅ OPENAI_API_KEY environment variable (save separately!)
- ✅ Poetry virtual environment requirements (in `pyproject.toml`)

### This Documentation
- ✅ Save this file: `WINDOWS_SETUP_DOCUMENTATION.md`

---

**Document Created**: October 1, 2025  
**System**: Windows 11 with WSL  
**Python Version**: 3.12  
**Voicepipe Version**: 0.1.0  
**Branch**: backup/windows-compatibility-20251001-221315
