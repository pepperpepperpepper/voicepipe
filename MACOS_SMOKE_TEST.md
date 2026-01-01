# macOS smoke test (manual)

This is a quick checklist to validate Voicepipe on macOS.

## 1) Install + config paths

- `voicepipe doctor env`
  - `env file path` should be `~/Library/Application Support/voicepipe/voicepipe.env`
  - `state dir` should be `~/Library/Application Support/voicepipe/state`
  - `logs dir` should be `~/Library/Logs/voicepipe`

## 2) Recording (mic)

- Start: `voicepipe start`
- Speak a few words
- Cancel (no transcription/API key needed): `voicepipe cancel`

If recording fails, confirm **Microphone** permission for the launching app (Terminal / Shortcuts / Automator).

## 3) Typing backend (Accessibility)

- `python3 -c 'from voicepipe.typing import type_text; print(type_text(\"hello from voicepipe\"))'`

Grant **Accessibility** permission to the launching app (Terminal / Shortcuts / Automator) if typing fails.

## 4) Hotkey binding (Quick Action / Shortcuts)

- Follow `hotkey-examples/macos-quick-action.md`
- Trigger the shortcut and confirm log output:
  - `~/Library/Logs/voicepipe/voicepipe-fast.log`

