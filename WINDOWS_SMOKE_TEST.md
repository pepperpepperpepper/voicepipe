# Windows Smoke Test Checklist (Win10/11, no WSL)

These checks are meant to validate the Windows “daemonless” path (subprocess + control file) and the hotkey target (`voicepipe-fast toggle`) without requiring API keys.

## Pre-flight

- Use an **interactive, unlocked** desktop session (typing and desktop tests won’t work from a service session).
- Ensure Python is on PATH (`python --version`) and that you installed Voicepipe (`voicepipe --help`).

## Config + paths

- `voicepipe doctor env` (verify it prints `%APPDATA%/%LOCALAPPDATA%/%TEMP%` and the resolved `env_file`/`state_dir`/`logs_dir`)
- `voicepipe config show` (verify the resolved env file path; default is `%APPDATA%\\voicepipe\\voicepipe.env`)
- `voicepipe service status` (should print an actionable “systemd is not available” message)

## Recording lifecycle (no API keys required)

- `voicepipe start`
- `voicepipe status` (expect: “recording (PID: …)”)
- `voicepipe cancel`
- `voicepipe status` (expect: “idle”)

Optional: validate stop-path cleanup/preservation without an API key:

- `voicepipe start`
- `voicepipe stop`
  - Expect a transcription error about missing API keys, and an extra line `Preserved audio file: ...` pointing at the preserved audio directory.

## Typing (no API keys required)

1. Open Notepad and click into it.
2. Run:
   - `python -c "from voicepipe.typing import type_text; ok, err = type_text('hello from voicepipe\\n'); print(ok, err)"`
3. Expect text to appear in Notepad.

Notes:
- Typing into elevated apps usually requires running Voicepipe elevated too (UIPI).
- If you see “No interactive desktop session available”, you’re likely running without a foreground window (locked session/service).

## Hotkey target (`voicepipe-fast toggle`)

- Run `voicepipe-fast toggle` twice:
  - 1st run starts recording.
  - 2nd run stops, attempts transcription, and (without keys) preserves the audio file.
- Check the log file:
  - `%LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log` (or override via `VOICEPIPE_FAST_LOG_FILE`)

## Native hotkey runner (Alt+F5)

Voicepipe includes a stdlib-only hotkey runner that registers **Alt+F5** and triggers `voicepipe-fast toggle`:

- Install at login (Startup folder shortcut):
  - `voicepipe hotkey install`
  - Log out/in (or reboot), then press Alt+F5 twice.
- Or run it manually (no console):
  - `pythonw -m voicepipe.win_hotkey`

Then:
- Press Alt+F5 once (start recording), then again (stop/transcribe/preserve).
- Check `%LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log` for `[HOTKEY]` lines.

## Optional: API key smoke

After setting `OPENAI_API_KEY` (or ElevenLabs key) in your `voicepipe.env`:

- `voicepipe start` → `voicepipe stop --type` (expect: transcript printed + typed into the focused app)
