# Voicepipe “Spoken Commands” Architecture Plan (Commands Deferred)

## Goal
Add the missing plumbing so Voicepipe can *reliably* produce a structured “what happened” artifact (recording + transcription + metadata), and introduce an **intent routing layer** that can later dispatch “spoken commands” — without adding any command execution yet.

## Non-Goals (for this plan)
- No command registry / command execution / automation actions yet.
- No changes to STT quality, model selection UX, or backend features beyond what is needed for structured results and routing.
- No UI work (systray, hotkey bindings, etc.) beyond keeping existing behavior working.

## Why This First
Today, Voicepipe’s core artifact is just “printed text”. Before commands exist, we need:
- deterministic stop/cancel behavior across daemon vs subprocess recording
- session ownership to prevent cross-session confusion
- a structured result format (for debugging, testing, and future “intent” decisions)
- a safe default intent router that does not break dictation

---

## Phase 0 — Baseline + Guardrails
**Objective:** tighten correctness without changing user behavior.

1) Fix recorder ownership selection in `AutoRecorderBackend`
- Problem: `AutoRecorderBackend.stop()` always tries the recorder daemon first. If the recorder daemon is reachable but idle while a subprocess recording is active, `stop` returns a daemon error instead of stopping the subprocess session.
- Change:
  - Make `AutoRecorderBackend.stop()` and `.cancel()` select the backend that actually “owns” the active recording:
    - If daemon status == recording → stop daemon.
    - Else if subprocess session exists → stop subprocess.
    - Else → error: no active session.
- Update tests to cover the daemon-idle + subprocess-recording case.

2) Unify “is recording?” checks
- Add a single helper (or method) that answers: “is there an active recording right now?” using:
  - daemon socket + daemon status
  - subprocess session file presence

**Acceptance criteria**
- `voicepipe start` then `voicepipe stop` works whether daemon is running or not.
- `voicepipe stop` correctly stops a subprocess recording even if daemon is present but idle.

---

## Phase 1 — Recording Identity (Session Ownership)
**Objective:** make it impossible to mix up audio/session boundaries once we start routing commands.

1) Introduce a `recording_id` (UUID-ish string)
- Recorder daemon:
  - On `start`, generate `recording_id`, store it in daemon state, return it in JSON.
  - On `stop`, return the same `recording_id` with `audio_file`.
- Subprocess recording:
  - Add `recording_id` into the session JSON file alongside `pid` and `audio_file`.

2) Thread the `recording_id` through the stop flow
- `StopResult` should carry `recording_id`.
- The CLI stop/dictate pipeline should attach `recording_id` to the later transcription artifact.

**Acceptance criteria**
- Both daemon and subprocess stop paths produce an audio file plus a stable `recording_id`.
- The recording id appears in the eventual structured output (Phase 2).

---

## Phase 2 — Structured Transcription Artifact (Keep Text UX)
**Objective:** move from “stdout text is the truth” to “stdout is a view of a structured object”.

1) Add `TranscriptionResult` dataclass (new module)
Suggested fields (start small, allow expansion):
- `text: str`
- `backend: str` (resolved backend actually used)
- `model: str` (resolved model actually used)
- `audio_file: str | None`
- `recording_id: str | None`
- `source: str` (e.g. `stop`, `dictate`, `transcribe-file`, `fast-toggle`)
- `warnings: list[str]`

2) Preserve existing API + add a new one
- Keep `transcribe_audio_file(...) -> str` for backward compatibility.
- Add `transcribe_audio_file_result(...) -> TranscriptionResult` (or similar) used by the CLI.

3) Add `--json` output mode (opt-in)
- On `voicepipe stop`, `voicepipe dictate`, `voicepipe transcribe-file`, and `voicepipe-fast` (optional):
  - Default remains printing just `text`.
  - `--json` prints the structured object as JSON.

**Acceptance criteria**
- Default behavior unchanged for users relying on piping (`voicepipe stop | wl-copy`).
- With `--json`, the output includes at least `text`, `backend`, `model`, and `recording_id` when available.

---

## Phase 3 — Intent Router (No Command Execution Yet)
**Objective:** introduce the *decision point* between dictation and command without acting on commands.

1) Add `IntentResult` + router API (pure function)
- Input: `TranscriptionResult` + minimal config.
- Output:
  - `mode: "dictation" | "command" | "unknown"`
  - `dictation_text: str | None`
  - `command_text: str | None` (the post-wakeword text; for later parsing)
  - `reason: str` (debuggable)

2) Initial routing policy: prefix-only (safe default)
- If transcript starts with a wake prefix (configurable, default: `command` and/or `computer`):
  - route to `mode="command"` and strip the prefix.
- Else:
  - route to `mode="dictation"` and pass through unchanged.

3) Wire it into the pipelines (behavior stays the same for now)
- For now, even if `mode="command"`, do NOT execute anything.
- Options:
  - Default: treat command-mode as dictation (after stripping prefix) so users don’t lose text.
  - Or add `VOICEPIPE_COMMANDS_STRICT=1` that refuses to type/print “command …” and instead errors (off by default).

**Acceptance criteria**
- Dictation flow remains stable.
- Users can opt-in to say “command …” and see routing info in `--json`.

---

## Phase 4 — Tests (Hermetic by Default)
**Objective:** make refactors safe without requiring mic/systemd/network.

1) Unit tests for backend selection + ownership logic
- Cover daemon present/idle + subprocess active.
- Cover daemon recording path.

2) Unit tests for result object + JSON output
- Validate stable keys.
- Ensure secrets are never printed.

3) Unit tests for intent router prefix behavior
- “command copy that” → `mode=command`, `command_text="copy that"`.
- “hello world” → `mode=dictation`.

4) Keep live tests opt-in
- Continue using `VOICEPIPE_LIVE_TESTS=1` for anything that hits mic/network/API keys.

---

## Open Questions (Decide Before Adding Commands)
- Wakeword policy: fixed prefixes vs configurable list vs “double-tap hotkey enables command mode”.
- Command-mode UX: should command-mode ever type text by default?
- Strictness: should unknown commands fall back to dictation or error?
- Where to store user command config (env file vs separate config file; permissions).

---

## Deliverables Checklist
- [x] Recorder ownership fix in `AutoRecorderBackend.stop/cancel`
- [x] `recording_id` in daemon + subprocess session
- [x] `TranscriptionResult` + `--json` output (default unchanged)
- [x] `IntentRouter` (prefix-only) returning `IntentResult`
- [x] Tests for ownership + result + router (offline)

