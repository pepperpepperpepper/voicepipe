# macOS Compatibility Plan (Voicepipe)

This plan mirrors the Windows work in `WINDOWS_COMPAT_PLAN.md`, but focuses on
macOS-specific gaps: paths, typing, hotkey binding, and service messaging.

## Goals (macOS MVP)

- macOS (Intel + Apple Silicon): `voicepipe start|stop|cancel|status|dictate|transcribe-file` work reliably.
- macOS: `voicepipe-fast toggle` works as the hotkey target (no systemd; logs to a file).
- macOS: `--type` works with an open, “native-ish” typing backend.
- macOS: `voicepipe config …` + `voicepipe setup` use macOS-appropriate paths and messaging.
- Offline `pytest` suite passes on macOS CI by default (no mic/network/API keys required).

## Non-goals (initial)

- Shipping a signed `.app` bundle or menubar UI by default.
- Shipping a launchd background service/daemon **by default** before measuring subprocess latency.
- Typing into secure/elevated contexts (password fields, Secure Input, etc.) beyond best-effort.

## Principles / Constraints

- **Performance-first**: no expensive probing in hot paths (hotkeys).
- **No regressions**: keep Linux + Windows behavior stable.
- Prefer **stdlib** (avoid `pyobjc`) unless it materially reduces bugs/maintenance.
- Assume **no shell init**: Shortcuts/Automator/LaunchAgents won’t load `.zshrc`/`.bashrc`.

## Known macOS pitfalls (design for these explicitly)

- **TCC permissions**:
  - Microphone access is required for recording.
  - Accessibility access is required for typing (System Events / event injection).
- **Secure Input**: typing may be blocked in some apps/fields.
- **Focus changes**: a hotkey runner can steal focus; typing must be careful about the target app.
- **Audio deps**: `sounddevice` uses PortAudio; Homebrew users often need `brew install portaudio`.

---

## Phase A — macOS paths + env file location

Goal: Voicepipe uses macOS-idiomatic directories by default, while keeping Linux stable.

Tasks:
- `voicepipe/config.py`:
  - macOS default env file: `~/Library/Application Support/voicepipe/voicepipe.env`
  - Always honor `VOICEPIPE_ENV_FILE` override.
  - Keep Linux default stable: `~/.config/voicepipe/voicepipe.env`
  - Optional: add a `voicepipe config migrate` path that detects a Linux-style env file on macOS and offers to copy it.
- `voicepipe/paths.py`:
  - macOS `state_dir`: `~/Library/Application Support/voicepipe/state`
  - macOS `logs_dir`: `~/Library/Logs/voicepipe` (or `.../Application Support/.../logs`; decide and document)
  - Keep runtime artifacts in the temp dir (per-user), as today.
- `voicepipe/commands/doctor.py`:
  - `voicepipe doctor env` prints macOS-relevant env vars (`HOME`, `TMPDIR`) and resolved dirs.
- Tests:
  - Add table-driven assertions for macOS path selection (mocked `sys.platform` + env).

Acceptance:
- On macOS: `voicepipe doctor env` prints sane, macOS-idiomatic `env_file`/`state_dir`/`logs_dir` without creating them.

---

## Phase B — Typing backend (macOS)

Goal: `--type` works on macOS in an interactive session.

MVP design:
- Add a new typing backend `osascript` that uses:
  - `osascript -e 'tell application \"System Events\" to keystroke ...'`
- Requirements:
  - The running process (Terminal, python, Automator, etc.) must be granted **Accessibility** permission.

Tasks:
- `voicepipe/typing.py`:
  - Add `osascript` backend name + selection logic (`auto` on macOS chooses `osascript`).
  - Implement `type_text` via `osascript`, including correct escaping and newline handling.
  - Optional: capture + re-activate the frontmost app before typing (bundle id / pid) so `voicepipe-fast toggle` can type back into the original app.
- Docs:
  - Add a macOS smoke checklist documenting required permissions.

Acceptance:
- On macOS: `voicepipe stop --type` types into the focused app after permissions are granted.

---

## Phase C — Hotkey binding (native as possible)

Goal: bind `voicepipe-fast toggle` to a global keyboard shortcut without third-party hotkey tools.

Recommended (native) approach:
- Create an Automator **Quick Action** (or a Shortcuts workflow) that runs `voicepipe-fast toggle`.
- Assign a keyboard shortcut in System Settings → Keyboard → Keyboard Shortcuts.

Tasks:
- Add `hotkey-examples/macos-quick-action.md` with a step-by-step setup.
- Ensure `voicepipe-fast` remains safe under no-TTY launches (stdout/stderr can be `None`).
- Ensure env-file loading is the contract (`voicepipe.env`), not shell exports.

Acceptance:
- Pressing the configured macOS shortcut triggers `voicepipe-fast toggle` and writes logs to the configured log file.

---

## Phase D — Service messaging / launchd

Goal: avoid systemd-centric guidance on macOS.

Tasks:
- `voicepipe service …`:
  - On macOS, fail fast with an actionable “systemd is not available on macOS; use LaunchAgents” message (parallel to Windows behavior).
- `voicepipe setup`:
  - When `systemctl` is missing on macOS, print LaunchAgent guidance instead of a generic “systemctl not found”.
- Optional:
  - Add a `voicepipe launchd` helper that generates/installs a LaunchAgent plist.

Acceptance:
- On macOS: `voicepipe service status` does not mention systemd installation steps.

---

## Phase E — CI (macOS)

Goal: keep macOS support from regressing.

Tasks:
- Add a `macos-latest` GitHub Actions job running:
  - `pytest -m "not live and not desktop"`
- Keep desktop-only tests (typing/focus) behind `@pytest.mark.desktop` and run them only on an interactive self-hosted runner (optional).

Acceptance:
- macOS CI is green on PRs without requiring mic/network/API keys.

---

## Packaging notes

- Add macOS classifiers to `pyproject.toml` once CI passes.
- Keep macOS-only features behind optional extras if we end up needing non-stdlib deps.
