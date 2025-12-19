# Voicepipe Refactor TODOs

This is a prioritized, actionable checklist for improving correctness, reliability, and maintainability.

Conventions:
- **P0**: correctness / data-loss / “can’t use it”
- **P1**: reliability / UX / hard-to-debug failures
- **P2**: maintainability / cleanup / future-proofing

---

## P0 — Correctness & Data Integrity

- [x] **Normalize daemon request parsing for `device`**
  - Problem: `voicepipe` CLI passes `device` as a string; daemon treats it as an index and may create `FastAudioRecorder(device_index="12")` (bad type) and/or skip device switching logic. (`voicepipe/cli.py`, `voicepipe/daemon.py`)
  - Tasks:
    - Parse `request.get("device")` into an `int | None` in `RecordingDaemon._start_recording`.
    - Return a structured error if parsing fails (e.g. `"device must be an integer"`).
  - Acceptance: `voicepipe start --device 12` reliably uses device 12; invalid values error cleanly.

- [x] **Fix “WAV fallback writes a different filename” mismatch**
  - Problem: daemon creates `... .mp3` file and returns that path, but WAV fallback writes `filename.replace('.mp3', '.wav')` and the returned `.mp3` may be empty/missing. (`voicepipe/daemon.py`, `voicepipe/recorder.py`)
  - Options:
    - Option A (preferred): always record to WAV internally and let transcriber accept WAV.
    - Option B: keep MP3 but ensure fallback writes to the exact returned path or also return the actual produced path + format.
  - Tasks:
    - Decide on canonical on-disk format (`wav` vs `mp3`) and make it consistent across:
      - `RecordingDaemon` responses
      - `voicepipe` CLI cleanup
      - `voicepipe-fast` cleanup
  - Acceptance: returned `audio_file` always exists and is readable by transcription.

- [x] **Remove duplicate timeout logic**
  - Problem: daemon sets its own 5-minute timer and `FastAudioRecorder` also has `max_duration` + timer; this can race/double-trigger stop/cleanup. (`voicepipe/daemon.py`, `voicepipe/recorder.py`)
  - Tasks:
    - Pick one timeout owner (daemon *or* recorder).
    - Ensure timeout behavior is consistent with UX: stop recording + preserve file for transcription, or cancel + delete.
  - Acceptance: after timeout, daemon returns stable state; no crashes; file handling is deterministic.

- [x] **Thread-safety: lock daemon state transitions**
  - Problem: each client connection spawns a thread; without a lock, `start/stop/cancel` can interleave and corrupt state (e.g. `self.audio_file` becomes `None` mid-stop). (`voicepipe/daemon.py`)
  - Tasks:
    - Add a `threading.Lock` and guard `recording/recorder/audio_file/timeout_timer/_timeout_triggered`.
    - Consider handling clients serially if simplicity beats parallelism (the workload is tiny).
  - Acceptance: rapid repeated hotkeys can’t crash or leave daemon “stuck”.

- [x] **Make socket reads robust and bounded**
  - Problem: daemon reads `conn.recv(1024)` once, assumes full JSON; client sends unframed JSON; larger payloads or partial frames can break. (`voicepipe/daemon.py`, `voicepipe/cli.py`, `voicepipe-fast`)
  - Tasks:
    - Implement bounded request reads (server-side) that tolerate partial frames.
    - (Optional follow-up) Define explicit message framing: newline-delimited JSON (`ndjson`) or length-prefix.
  - Acceptance: commands work even if JSON arrives in multiple chunks.

---

## P1 — Reliability, UX, and Debuggability

- [x] **Unify IPC client logic (`voicepipe` + `voicepipe-fast`)**
  - Problem: two separate implementations of daemon IPC with different timeouts, error handling, and JSON reading. (`voicepipe/cli.py`, `voicepipe-fast`)
  - Tasks:
    - Create `voicepipe/ipc.py` (or similar) with:
      - `send_request(command, **kwargs) -> dict`
      - consistent timeouts
      - consistent error formatting
    - Update `voicepipe/cli.py` to use it.
    - Optionally migrate `voicepipe-fast` into the package as `voicepipe fast` or `voicepipe hotkey` so it can import the shared IPC client.
  - Acceptance: one authoritative IPC path; fewer drift bugs.

- [x] **Replace `print()` with `logging` and add a `--debug` mode**
  - Problem: logs go to stderr inconsistently; daemon/service logs should be structured and optional. (`voicepipe/daemon.py`, `voicepipe/cli.py`, `voicepipe/recorder.py`, `transcriber_daemon.py`)
  - Tasks:
    - Add module loggers and a helper to configure level/format.
    - Ensure systemd services can enable debug via env var (e.g. `VOICEPIPE_LOG_LEVEL=DEBUG`).
  - Acceptance: easy to capture useful logs without spamming normal CLI use.

- [x] **Make paths XDG-correct and per-user**
  - Problem: mixed use of `/tmp/voicepipe` and `XDG_RUNTIME_DIR`; potential collisions across users and leftover files. (`voicepipe/recorder.py`, `voicepipe/daemon.py`, `transcriber_daemon.py`, `voicepipe-fast`)
  - Tasks:
    - Standardize:
      - runtime socket dir: `XDG_RUNTIME_DIR` (fallback `/run/user/$UID`, then `/tmp`)
      - transient audio dir: `${XDG_RUNTIME_DIR}/voicepipe` or `${XDG_CACHE_HOME}/voicepipe`
      - state dir: `${XDG_STATE_HOME}/voicepipe` or `${XDG_RUNTIME_DIR}/voicepipe`
    - Centralize in `voicepipe/paths.py`.
  - Acceptance: no `/tmp` collisions; clearer cleanup policy.

- [x] **Systray cancel should actually cancel**
  - Problem: systray menu includes “Cancel” but `_on_cancel` is not wired to anything. (`voicepipe/systray.py`)
  - Tasks:
    - Decide: remove cancel menu item or make it send a cancel IPC request.
    - If implementing: ensure it uses the same IPC module and socket path.
  - Acceptance: clicking “Cancel” stops recording and updates daemon state.

- [x] **Make `doctor` self-contained and safe**
  - Problem: doctor performs record/transcribe and may leave artifacts; also mixes concerns. (`voicepipe/cli.py`)
  - Tasks:
    - Split into subchecks: `doctor env`, `doctor audio`, `doctor daemon`.
    - Always preserve the record-test output unless `--cleanup` is passed.
  - Acceptance: diagnostics never destroy data unexpectedly.

- [x] **Clarify cleanup policy**
  - Problem: `voicepipe stop` always unlinks `audio_file` even if transcription fails; daemon sometimes preserves; fast script sometimes preserves. (`voicepipe/cli.py`, `voicepipe/daemon.py`, `voicepipe-fast`)
  - Tasks:
    - Define policy: if transcription fails, preserve audio + write a pointer to it in a stable location.
    - Implement consistently across all entrypoints.
  - Acceptance: no accidental data-loss when OpenAI fails/network down.

---

## P2 — Maintainability & Project Hygiene

- [x] **Remove unused dependencies or use them**
  - `httpx` and `python-dotenv` are declared but unused. (`pyproject.toml`)
  - Tasks:
    - Either remove them, or add `.env` loading (e.g. in CLI entry) to justify `python-dotenv`.
  - Acceptance: dependency list matches code usage.

- [x] **Align type-checking policy with reality**
  - Problem: `mypy` is configured with `disallow_untyped_defs=true`, but the codebase is mostly untyped. (`pyproject.toml`)
  - Options:
    - Option A: relax mypy to “gradual typing” (preferred short-term).
    - Option B: add types module-by-module and keep strict settings.
  - Acceptance: CI/dev tooling gives useful signal, not noise.

- [x] **Consolidate scripts into package CLIs**
  - Problem: `voicepipe-fast`, `voicepipe-transcribe-file`, `transcriber_daemon.py`, and experimental systray scripts live at repo root. (`voicepipe-fast`, `voicepipe-transcribe-file`, `transcriber_daemon.py`, `systray_icon.py`, `systray_toggle.py`)
  - Tasks:
    - Move “supported” scripts into `voicepipe/` with proper console scripts in `pyproject.toml`.
    - Mark experimental scripts clearly or move to `examples/`.
  - Acceptance: “what’s supported?” is obvious.

- [x] **Update README + examples**
  - Problem: README mentions PyAudio/WAV and has outdated tmp/state file patterns; hotkey examples have hard-coded `/home/pepper`. (`README.md`, `hotkey-examples/*`)
  - Tasks:
    - Replace hard-coded paths with `$HOME` examples.
    - Document daemon vs fallback modes and the sockets used.
    - Document transcription model choices and cost/perf tradeoffs briefly.
  - Acceptance: new user can install and run without guesswork.

- [x] **Add minimal tests for pure logic**
  - This codebase currently has no tests.
  - Suggested first tests (no audio hardware required):
    - Path resolution (`runtime_dir`, temp/state dir helpers).
    - Device parsing/validation.
    - IPC framing encode/decode helpers.
  - Acceptance: `pytest` runs in CI and catches regressions in glue logic.

---

## Nice-to-Have / Future Work

- [ ] Consider switching MP3 encoding to Opus/WebM or FLAC for better quality/size tradeoff (verify OpenAI API accepts chosen format).
- [ ] Add Wayland-first typing support (xdotool is X11; consider `wtype` on Wayland).
- [ ] Add a “push-to-talk” / “hold-to-record” mode for WMs.
