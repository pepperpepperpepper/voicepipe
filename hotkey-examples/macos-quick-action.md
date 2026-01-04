# macOS hotkey (Quick Action / Shortcuts)

Goal: run `voicepipe-fast toggle` from a global keyboard shortcut without third-party hotkey apps.

## Prereqs

- Voicepipe installed for your user (so `voicepipe-fast` is on PATH), or know the full path to it.
- Config file exists at the macOS default:
  - `~/Library/Application Support/voicepipe/voicepipe.env`
  - (or set `VOICEPIPE_ENV_FILE` to override)

## Option A — Automator “Quick Action” (recommended)

If you already have Voicepipe installed, the fastest path is:

```sh
voicepipe hotkey install
```

Then assign a keyboard shortcut in System Settings (steps below).

1. Open **Automator** → **New Document** → **Quick Action**.
2. Set:
   - “Workflow receives” → **no input**
   - “in” → **any application**
3. Add action: **Run Shell Script**.
4. Set shell to **/bin/zsh** (or **/bin/bash**).
5. Script:

   ```sh
   voicepipe-fast toggle
   ```

   If `voicepipe-fast` is not found, either:

   - Run it via Python (often easiest under Automator/Shortcuts):

   ```sh
   python3 -m voicepipe.fast toggle
   ```

   - Or add your user scripts directory to `PATH` (pip `--user` installs here):

   ```sh
   export PATH="$HOME/Library/Python/3.9/bin:$PATH"
   voicepipe-fast toggle
   ```

6. Save as: `Voicepipe Toggle`.
7. Assign a keyboard shortcut:
   - System Settings → Keyboard → Keyboard Shortcuts → (Services / Quick Actions) → find `Voicepipe Toggle`.

Logs (for debugging):
- `~/Library/Logs/voicepipe/voicepipe-fast.log`

## Option B — Shortcuts app

1. Open **Shortcuts** → create a new shortcut.
2. Add action: **Run Shell Script** (or **Run AppleScript**, if you prefer).
3. Command: `voicepipe-fast toggle` (or `python3 -m voicepipe.fast toggle`).
4. Assign a keyboard shortcut in the shortcut details panel.
