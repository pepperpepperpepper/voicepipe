# macOS Compatibility Plan (Voicepipe)

This plan mirrors the Windows work in `WINDOWS_COMPAT_PLAN.md`, but focuses on
macOS-specific gaps: paths, typing, hotkey binding, and service messaging.

## Goals (macOS MVP)

- macOS (Intel + Apple Silicon): `voicepipe start|stop|cancel|status|dictate|transcribe-file` work reliably.
- macOS: `voicepipe-fast toggle` works as the hotkey target (no systemd; logs to a file).
- macOS: `--type` works with an open, “native-ish” typing backend.
- macOS: `voicepipe config …` + `voicepipe setup` use macOS-appropriate paths and messaging.
- Offline `pytest` suite passes on macOS CI by default (no mic/network/API keys required).

## Top Priority (P0) — In-memory audio capture/transcribe

Goal: eliminate temp WAV file I/O on hotkey/dictation paths while staying cross-platform (no tmpfs requirement on Windows).

Approach:
- Record audio into memory (PCM/WAV bytes) and send bytes to STT (OpenAI supports `file=bytes` / tuple upload).
- Use `tempfile.SpooledTemporaryFile` as a safety valve (RAM-first, auto-spill to disk for large recordings).
- Keep a debug fallback to “preserve last audio to disk” on transcription failure.

Acceptance:
- 5-minute recordings work without requiring tmpfs on Windows/macOS/Linux.
- Hotkey path avoids writing temp WAVs in the common case.

## Non-goals (initial)

- Shipping a signed `.app` bundle or menubar UI by default.
- Shipping a launchd background service/daemon **by default** before measuring subprocess latency.
- Typing into secure/elevated contexts (password fields, Secure Input, etc.) beyond best-effort.

## Principles / Constraints

- **Performance-first**: no expensive probing in hot paths (hotkeys).
- **No regressions**: keep Linux + Windows behavior stable.
- Prefer **stdlib** (avoid `pyobjc`) unless it materially reduces bugs/maintenance.
- Assume **no shell init**: Shortcuts/Automator/LaunchAgents won’t load `.zshrc`/`.bashrc`.

## Appendix — Windows hotkey / agent architecture notes

Constraint: on Windows, a global hotkey (e.g. Alt+F5) requires a resident process to register/listen for it.

Current behavior (Windows hotkey runner):
- Startup launches `pythonw -m voicepipe.win_hotkey` and it stays running.
- First use after boot/login may be slower (cold Python start/import + first audio init + first network/TLS).
- Subsequent hotkey presses are faster because the runner is already warm.
- There is no separate “Voicepipe server/daemon” in this design; the hotkey runner is the long-lived process.

Better next-step architecture (recommended):
- Split into two processes:
  - **Hotkey shim**: tiny, stable process that only does `RegisterHotKey` and sends an IPC message.
  - **Voicepipe agent**: long-lived per-user process that owns record/stop/transcribe/type and exposes an IPC API.
- Use **Windows Named Pipes** for IPC (Windows-native; conceptually similar to Unix sockets on Linux).
- Benefits: hotkey reliability (shim stays simple), agent can keep expensive state warm (audio selection/client),
  and the agent can be restarted independently if it crashes.

Alternative (most native + fastest cold start, but more work):
- Ship a single native tray app (Rust/C++/.NET) that does hotkey + audio + STT + typing.

Usually not recommended:
- A Windows Service (Session 0) for this use-case; it cannot reliably interact with the user desktop for typing,
  so you still need a per-user desktop component.

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
