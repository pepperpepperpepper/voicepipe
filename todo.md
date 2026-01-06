# Voicepipe Windows Hotkey Troubleshooting Notes

## Context
- System: Windows 10/11 (no WSL runtime), interactive desktop available.
- Repo path: `C:\Users\fenlo\Downloads\voicepipe`
- Hotkey target: `voicepipe-fast toggle` (recommended by repo docs for Windows).
- Keys file: `C:\Users\fenlo\.api-keys` (OPENAI_API_KEY, ELEVENLABS_API_KEY).

## Installation Notes (Poetry / Windows)
- Python 3.11 installed (system python at `C:\Users\fenlo\AppData\Local\Programs\Python\Python311\python.exe`).
- Poetry installed via pipx (pip install of poetry package failed with `CredRead`/file lock errors):
  ```powershell
  python -m pip install --user pipx
  python -m pipx ensurepath
  python -m pipx install poetry
  ```
- Repo dependencies installed from the repo root:
  ```powershell
  cd C:\Users\fenlo\Downloads\voicepipe
  $env:POETRY_KEYRING_ENABLED="false"
  poetry install
  ```
- AutoHotkey installed via Chocolatey (v2 runtime at `C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe`).
- `voicepipe.env` created/updated at `C:\Users\fenlo\AppData\Roaming\voicepipe\voicepipe.env` with:
  - `OPENAI_API_KEY=...`
  - `ELEVENLABS_API_KEY=...`
  - `VOICEPIPE_RECORDING_INIT_TIMEOUT=15`

## What Works
- `poetry run voicepipe smoke` succeeds.
- `poetry run voicepipe dictate --seconds 5 --type` works when run manually in PowerShell.

## What Does NOT Work (current)
- Win+Shift+V hotkey does not produce typed output in the active window.
- Hotkey feels slow to respond.

## Hotkey Script (AutoHotkey v2)
File: `C:\Users\fenlo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\voicepipe-hotkey.ahk`

```ahk
#Requires AutoHotkey v2.0
#SingleInstance Force

; Win+Shift+V toggles Voicepipe (records on first press, stops/transcribes on second press).
#+v::
{
    EnvSet("POETRY_KEYRING_ENABLED", "false")
    python := "C:\\Users\\fenlo\\AppData\\Local\\pypoetry\\Cache\\virtualenvs\\voicepipe-5_jkLVzU-py3.11\\Scripts\\python.exe"
    cmd := Chr(34) . python . Chr(34) . " -m voicepipe.fast toggle"
    Run(cmd, "C:\\Users\\fenlo\\Downloads\\voicepipe", "Hide")
}
```

### AutoHotkey v2 Requirement
- The script is **AHK v2** syntax and will not load in AHK v1.
- `.ahk` file association currently points to `AutoHotkeyUX.exe` (v1 launcher), so double‑clicking can run the wrong engine.
- Recommended launch command (PowerShell):
  ```powershell
  & "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" "C:\Users\fenlo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\voicepipe-hotkey.ahk"
  ```

## Log Findings
Log file: `C:\Users\fenlo\AppData\Local\voicepipe\logs\voicepipe-fast.log`

Recent lines show:
- Hotkey invocations **are** reaching `voicepipe-fast`.
- Recording sometimes times out on start: `Timed out waiting for recording subprocess to initialize (no session file).`
- Transcription succeeds, but typing fails due to a ctypes error:
  - `sendinput error: module 'ctypes.wintypes' has no attribute 'ULONG_PTR'`

Excerpt:
```
[TOGGLE] Starting recording...
[TOGGLE] Recording error: Timed out waiting for recording subprocess to initialize (no session file).
...
[TOGGLE] Transcription: Hello, hello, hello.
[TOGGLE] Warning: typing failed: sendinput error: module 'ctypes.wintypes' has no attribute 'ULONG_PTR'
```

## Likely Root Causes
1) **Typing backend error** in `voicepipe/typing.py` on Python 3.11:
   - `wintypes.ULONG_PTR` is missing in this runtime.
   - This prevents typing even when transcription succeeds.

2) **Recording startup timeout** intermittently triggers:
   - Start call may time out before the session file is written.
   - `VOICEPIPE_RECORDING_INIT_TIMEOUT=15` added, but log still shows timeouts.

3) **AHK v1/v2 conflict**:
   - AHK v1 service process exists and may interfere with hotkey registration.
   - Ensure the v2 engine is used for the script.

## Run Notes (manual + hotkey)
- Manual run (works): 
  ```powershell
  cd C:\Users\fenlo\Downloads\voicepipe
  $env:POETRY_KEYRING_ENABLED="false"
  poetry run voicepipe dictate --seconds 5 --type
  ```
- Hotkey run (intended):
  - Launch AHK v2 explicitly:
    ```powershell
    & "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" "C:\Users\fenlo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\voicepipe-hotkey.ahk"
    ```
  - Use Win+Shift+V twice in a non-elevated app (start/stop).

## Repo Updates Needed (to commit)
1) **Recording init timeout config**:
   - `voicepipe/recording_backend.py` now reads `VOICEPIPE_RECORDING_INIT_TIMEOUT`.
   - Keep this change and document it in Windows troubleshooting docs.
2) **Typing backend fix** (not yet applied):
   - `voicepipe/typing.py` needs a safe fallback for `wintypes.ULONG_PTR`.
   - This is likely the reason typing fails in hotkey mode.
3) **Docs update**:
   - Add AHK v2 requirement and the exact hotkey script to `WINDOWS_SMOKE_TEST.md` or `README.md`.
   - Note that double‑clicking `.ahk` may run v1 unless AHK v2 is used explicitly.

## Recommended Fixes / Next Steps
1) Fix SendInput typing error:
   - Patch `voicepipe/typing.py` to provide a fallback for `ULONG_PTR`:
     - Example:
       ```python
       try:
           ULONG_PTR = wintypes.ULONG_PTR
       except AttributeError:
           ULONG_PTR = ctypes.c_size_t
       ```
     - Then use `ULONG_PTR` for the `dwExtraInfo` field.

2) Verify hotkey actually fires:
   - Temporarily add a `MsgBox "hotkey fired"` to the script.
   - If no dialog appears, hotkey hook is not active.

3) Diagnose recording start timeout:
   - Confirm `VOICEPIPE_RECORDING_INIT_TIMEOUT` is loaded (print in debug or add to log).
   - Check `%TEMP%\voicepipe` for session files when a timeout occurs.

4) Reduce startup latency:
   - The script already avoids `poetry run` and uses the venv python directly.
   - If still slow, consider keeping a background recorder daemon (not yet supported on Windows).

## Tests To Run (after fixes)
1) `poetry run voicepipe smoke`
2) `poetry run voicepipe dictate --seconds 5 --type` (typing should work)
3) Hotkey toggle in Notepad (Win+Shift+V twice)
4) Check `voicepipe-fast.log` for errors

## Quick Commands
- Start AHK v2 script:
  ```powershell
  & "C:\Program Files\AutoHotkey\v2\AutoHotkey64.exe" "C:\Users\fenlo\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\voicepipe-hotkey.ahk"
  ```
- Tail fast log:
  ```powershell
  Get-Content "$env:LOCALAPPDATA\voicepipe\logs\voicepipe-fast.log" -Tail 50
  ```
