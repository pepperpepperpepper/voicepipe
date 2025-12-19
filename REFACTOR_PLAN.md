# Voicepipe Refactor Plan (Post systemd/API-key work)

This plan targets maintainability and reducing operational complexity beyond the systemd/API-key refactor already completed on the `refactor` branch.

## High-impact refactors (recommended order)

1) [x] **Split and de-duplicate the giant CLI**
   - Current pain: `voicepipe/cli.py` mixes config, service management, recording control, typing, and doctor logic in one file.
   - Refactor:
     - Move click command groups into modules (e.g. `voicepipe/commands/config.py`, `voicepipe/commands/service.py`, `voicepipe/commands/doctor.py`, `voicepipe/commands/recording.py`).
     - Keep `voicepipe/cli.py` as a thin Click entrypoint wiring those groups together.
     - Create shared helpers for:
       - consistent error formatting/exit codes
       - “print to stderr vs stdout” conventions
       - common subprocess wrappers (`xdotool`, `journalctl`, etc.)

2) [x] **Introduce a “recording backend” abstraction**
   - Current pain: `start/stop/status/cancel` duplicate “try daemon, else subprocess” logic and differ in behavior/error handling.
   - Refactor:
     - Add a `RecorderBackend` interface with methods: `start(device)`, `stop()`, `cancel()`, `status()`.
     - Implement:
       - `DaemonRecorderBackend` (uses `voicepipe/ipc.py`)
       - `SubprocessRecorderBackend` (uses `RecordingSession` + the `_record` subprocess)
     - Use one selection point (“best available backend”) so CLI commands become thin wrappers.

3) [x] **Consolidate recorder implementations**
   - Current pain: `voicepipe/recorder.py` has `FastAudioRecorder` and `AudioRecorder` with duplicated stream/ffmpeg lifecycle and timeout handling.
   - Refactor:
     - Merge into one recorder class with optional modes (wav vs mp3, pre-open vs not).
     - Ensure the recorder is usable by both:
       - `voicepipe/daemon.py` (daemon mode)
       - the CLI recording subprocess (`voicepipe/cli.py` -> `_record`)
     - Centralize timeouts and shutdown semantics in one place.

4) [x] **Unify transcription path (and optionally leverage transcriber daemon)**
   - Current pain: transcription is performed differently in `voicepipe/cli.py` vs `voicepipe/fast.py` (and error handling differs).
   - Refactor:
     - Add a shared helper like `transcribe_audio(audio_file, *, model, language, prompt, temperature)`:
       - Prefer transcriber daemon if available
       - Fall back to direct `WhisperTranscriber` call
     - Use the same helper from:
       - `voicepipe/cli.py` (`stop`, `transcribe-file`, doctor transcribe tests)
       - `voicepipe/fast.py` (hotkey path)

5) [x] **Centralize “type text” support + add Wayland options**
   - Current pain: xdotool logic is duplicated and assumes X11.
   - Refactor:
     - Add `voicepipe/typing.py` with:
       - X11 typing via `xdotool`
       - Optional Wayland typing via `wtype` (or `ydotool`) when available
     - Update `voicepipe/cli.py` and `voicepipe/fast.py` to use the shared helper and emit consistent errors.

6) [x] **Tighten runtime dir/socket permissions for non-systemd environments**
   - Current pain: when falling back to a global temp dir, runtime artifacts can be more permissive than necessary.
   - Refactor:
     - Ensure runtime artifact directories are created `0700` and sensitive files `0600` where applicable.
     - Ensure socket file ownership/permissions are appropriate on creation.
     - Keep behavior safe across:
       - `XDG_RUNTIME_DIR`
       - `/run/user/$UID`
       - `/tmp/voicepipe-$UID` fallback

7) [x] **Remove install/service duplication**
   - Current pain: systemd unit generation exists in multiple places (`install.sh`, templates, and `voicepipe/systemd.py`).
   - Refactor:
     - Make `install.sh` delegate to `voicepipe service install` and stop writing units directly.
     - Use one canonical system for units (rendered in Python via `voicepipe/systemd.py`).
     - Introduce a `voicepipe.target` so starting/stopping “Voicepipe” is one systemd command.

## Suggested sequencing / checkpoints

- Phase 1: CLI split + shared helpers (low risk, big readability win)
- Phase 2: RecorderBackend abstraction (reduces duplicated logic + makes behavior consistent)
- Phase 3: Recorder consolidation (medium risk; verify with `voicepipe doctor daemon --record-test`)
- Phase 4: Unify transcription + typing (improves hotkey reliability, reduces drift)
- Phase 5: Permissions hardening + installer simplification
