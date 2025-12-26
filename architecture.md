# Voicepipe Architecture

This document describes Voicepipe’s current architecture as implemented on the `zwingli` branch, plus the **agreed design direction** for the next refactor(s). It is intentionally written as a collaboration artifact: it describes what exists today, what the interfaces are, and what decisions are “locked in” vs. still open.

## 1) Product Goals (What Voicepipe Is)

Voicepipe is a Linux voice-to-text tool optimized for hotkey workflows:

- You explicitly start/stop recording (or use a “toggle” hotkey).
- Audio is transcribed (via a daemon when available).
- The resulting text is either printed to stdout, typed into the active app, or both.
- Optional “zwingli mode” post-processes the transcript with an LLM and outputs the LLM result instead.

Non-goal: always-listening wakeword detection. Voicepipe does **not** continuously record or stream audio to external APIs. Network calls happen only after recording is stopped (or when transcribing a file).

## 2) Major Components

### A. CLI (click)

Entry point: `voicepipe/cli.py` (console script: `voicepipe`).

Key commands (implemented under `voicepipe/commands/`):

- Recording lifecycle: `start`, `stop`, `status`, `cancel`, `dictate`, `transcribe-file`
- System integration: `service …`, `setup`, `doctor`, `smoke`
- Config management: `config show`, `config edit`, `config edit-settings`, `config set-openai-key`, `config set-elevenlabs-key`, `config set-groq-key`, `config migrate`

### B. Recorder

Two interchangeable recording backends:

1) **Recorder daemon** (preferred when running):
   - Module: `voicepipe/daemon.py` (`RecordingDaemon`)
   - Transport: Unix socket (`voicepipe.paths.daemon_socket_path()`)
   - Produces: a `.wav` file in the runtime dir and a `recording_id`

2) **Subprocess recorder** (fallback):
   - Module: `voicepipe/recording_subprocess.py` (invoked via hidden command `voicepipe _record`)
   - Uses `voicepipe/session.py` to write a small session file containing `pid`, `audio_file`, `recording_id`

Selection/fallback logic lives in:
- `voicepipe/recording_backend.py` (`AutoRecorderBackend`, `DaemonRecorderBackend`, `SubprocessRecorderBackend`)

### C. Transcriber

Transcription also has two modes:

1) **Transcriber daemon** (preferred when running):
   - Module: `voicepipe/transcriber_daemon.py`
   - Transport: newline-delimited JSON over a Unix socket (`voicepipe.paths.transcriber_socket_path()`)
   - Benefit: avoids SDK cold-start latency by keeping clients initialized

2) **Direct transcription (fallback)**:
   - Module: `voicepipe/transcription.py`
   - Backends:
     - OpenAI STT: `voicepipe/transcriber.py` (`WhisperTranscriber`)
     - ElevenLabs STT: `voicepipe/elevenlabs_transcriber.py` (`ElevenLabsTranscriber`)

The “prefer daemon but fall back” behavior is centralized in:
- `voicepipe/transcription.py` (`transcribe_audio_file`, `transcribe_audio_file_result`)

### D. Intent Router + Zwingli Post-Processor

After transcription, Voicepipe runs an intent step (if enabled):

- Module: `voicepipe/intent_router.py`
- Output: `IntentResult(mode="dictation"|"command"|"unknown", …)`

If intent resolves to `"command"`, Voicepipe runs the zwingli post-processor:

- Module: `voicepipe/zwingli.py` (`process_zwingli_prompt`)
- Uses the OpenAI Python SDK against OpenAI-compatible endpoints.
- Backends: `groq` (default) and `openai` (configurable).
- Input is **only** the transcript after stripping the wake prefix (the prefix is not sent)
- Output is the exact text to print/type (multi-line allowed)

Important: zwingli mode is text generation, not automation. There is no “command execution” layer yet.

### E. Output / Typing

- Module: `voicepipe/typing.py`
- X11: `xdotool type --clearmodifiers`
- Wayland: best-effort `wtype`

### F. Hotkey-Oriented “Fast Path”

Entry point: `voicepipe/fast.py` (console scripts: `voicepipe-fast`, `voicepipe-toggle`).

Design goals:
- Minimal startup overhead
- Works well when stderr is not visible (WM hotkeys)
- Captures the active window before transcription to type back into the right app
- Adds file locking + debounce to avoid double-trigger toggles

## 3) Data Contracts

### TranscriptionResult

Defined in: `voicepipe/transcription_result.py`

Core fields:
- `text`: raw transcript
- `backend`: resolved STT backend (`openai`|`elevenlabs`)
- `model`: resolved STT model id
- `audio_file`, `recording_id`, `source`, `warnings`

### IntentResult

Defined in: `voicepipe/intent_router.py`

Core fields:
- `mode`: `dictation|command|unknown`
- `dictation_text` or `command_text`
- `reason`: debug string

### “output_text”

Where it appears:
- CLI `--json` output: `voicepipe/commands/recording.py`
- Fast path JSON: `voicepipe/fast.py` (via `VOICEPIPE_FAST_JSON=1`)

Meaning:
- `output_text` is the final text Voicepipe would print/type after intent routing and zwingli processing.

### `zwingli` object (present in JSON payloads)

When the transcript is routed into zwingli mode, JSON outputs include a `zwingli` object describing what happened. This makes it easier to debug “why did it output this text?” without scraping stderr logs.

Proposed minimum fields (include anything else the provider returns that is useful):
- `backend`: e.g. `groq` / `openai`
- `model`: model id used
- `temperature`: resolved temperature
- `daemon_used`: `true|false` (or a nested `daemon` object)
- `duration_ms`: total wall time for zwingli processing
- `provider`: stable provider metadata (best-effort; fields present only when available)
  - `base_url`: API base URL or host (if known)
  - `request_id`: provider request id (if available)
  - `usage`: token usage (if available)
- `finish_reason`: provider finish reason (if available)
- `error`: string (only present on failure)

Privacy note:
- Do not include prompts/messages in JSON by default (or their hashes). If we ever need prompt visibility for debugging, it should be an explicit opt-in debug mode.

JSON schema note:
- Prefer a mostly-flat payload (top-level transcription fields + `intent` + `output_text` + `zwingli`) rather than fully nested objects.

## 4) Typical End-to-End Flows

### A) `voicepipe start` → `voicepipe stop [--type]`

1. `start` calls `AutoRecorderBackend.start()`
   - Prefers daemon; falls back to subprocess
2. `stop` calls `AutoRecorderBackend.stop()`
   - Stops whichever backend is actually recording
3. Transcribe (`transcribe_audio_file_result`)
   - Prefer transcriber daemon; fall back to direct STT
4. Route intent (`route_intent`) if enabled
5. If intent is command-mode:
   - Call `process_zwingli_prompt(command_text)`
6. Print `output_text` (and optionally type it)
7. Cleanup audio file on success; preserve on failure under `XDG_STATE_HOME/voicepipe/audio/`

### B) `voicepipe dictate`

Single command that records then stops/transcribes, with the same routing and zwingli behavior as `stop`.

### C) `voicepipe transcribe-file`

Transcribes an existing file, then applies routing/zwingli, prints/types output.

### D) `voicepipe-fast toggle` (one-key workflow)

1. Debounce + lock to avoid repeated triggers
2. Ask recorder daemon status via IPC
3. If recording:
   - Capture target window id (X11)
   - Stop recorder daemon
   - Transcribe
   - Route intent + zwingli
   - Type output into captured window
4. If not recording:
   - Start recorder daemon

## 5) IPC Protocols

### Recorder daemon protocol (Unix socket JSON)

Socket: `voicepipe.paths.daemon_socket_path()`

- Request: JSON object (single payload)
  - `{"command": "start", "device": 12}`
  - `{"command": "stop"}`
  - `{"command": "status"}`
  - `{"command": "cancel"}`

- Response: JSON object
  - Includes `status`, and on stop includes `audio_file` and `recording_id`

### Transcriber daemon protocol (NDJSON streaming)

Socket: `voicepipe.paths.transcriber_socket_path()`

- Client sends one JSON line:
  - `{"audio_file": "...", "model": "openai:gpt-4o-transcribe", "temperature": 0.0, ...}`
- Server streams JSON lines:
  - `{"type":"transcription","text":"...\\n"}`
  - `{"type":"complete"}`
  - or `{"type":"error","message":"..."}`

## 6) Configuration (Systemd-Friendly)

Voicepipe must work under systemd user services, which do not source shell init files like `.bashrc`.

### Files

- Secrets + env vars: `~/.config/voicepipe/voicepipe.env` (0600)
- Non-secret settings: `~/.config/voicepipe/config.toml` (0600)

### systemd user units

Installed via `voicepipe service install` (writes to `~/.config/systemd/user/` using `voicepipe/systemd.py`):

- `voicepipe-recorder.service`: recording daemon (`python -m voicepipe.cli daemon`)
- `voicepipe-transcriber.service`: transcription daemon (`python -m voicepipe.transcriber_daemon`)
- `voicepipe.target`: group unit that starts both

All units use `EnvironmentFile=-%h/.config/voicepipe/voicepipe.env` so API keys work under systemd without relying on shell init files.

Planned:
- `voicepipe-zwingli.service`: keeps zwingli’s LLM client warm for lower-latency hotkey workflows.

### Runtime vs. state paths

- Runtime artifacts (sockets, temp wav files, session markers): `voicepipe.paths.runtime_app_dir()` (prefers `XDG_RUNTIME_DIR`)
- Persistent state (preserved audio on failure, doctor artifacts): `voicepipe.paths.state_dir()` (uses `XDG_STATE_HOME`)

Config reload policy:
- Live reload is not required.
- CLI reads config on each invocation.
- Daemons read config at startup; apply changes with `voicepipe service restart` (or restarting the specific service).

Helpers:
- `voicepipe config edit` edits `voicepipe.env`
- `voicepipe config edit-settings` edits `config.toml`
- `voicepipe config show` prints resolved config state (never prints secrets)

### Precedence (high → low)

1. Process environment variables
2. `voicepipe.env` (loaded via `python-dotenv`)
3. `config.toml` (loaded via `tomllib`/`tomli`)
4. Built-in defaults

### Relevant knobs (current + planned)

Intent routing:
- `VOICEPIPE_INTENT_ROUTING=1|0` (env) or `[intent].routing_enabled` (TOML)
- `VOICEPIPE_INTENT_WAKE_PREFIXES=zwingli,...` (env) or `[intent].wake_prefixes=[...]` (TOML)

Zwingli processing:
- `VOICEPIPE_ZWINGLI_MODEL=...` (env) or `[zwingli].model` (TOML) (implemented; default: `moonshotai/kimi-k2-instruct` on Groq, `gpt-4o-mini` on OpenAI)
- `VOICEPIPE_ZWINGLI_TEMPERATURE=...` (env) or `[zwingli].temperature` (TOML)
- `VOICEPIPE_ZWINGLI_USER_PROMPT=...` (env) or `[zwingli].user_prompt` (TOML)
- `VOICEPIPE_ZWINGLI_SYSTEM_PROMPT=...` (env) or `[zwingli].system_prompt` (TOML)
- `VOICEPIPE_ZWINGLI_BACKEND=groq|openai` or `[zwingli].backend` (implemented; LLM backend is separate from STT backend; default: `groq`)
- `[zwingli].base_url` (implemented; set in `config.toml` for OpenAI-compatible endpoints; default: `https://api.groq.com/openai/v1`; no env var)

Zwingli credentials (stored in `voicepipe.env` or systemd credentials):
- Groq backend: `GROQ_API_KEY=...`
- OpenAI backend: `OPENAI_API_KEY=...`

Error reporting:
- `VOICEPIPE_ERROR_REPORTING=1|0` or `[errors].reporting_enabled`

Strict mode safety valve:
- `VOICEPIPE_COMMANDS_STRICT=1` refuses output if command-mode is detected (and skips the zwingli LLM call).

## 7) Zwingli Mode: Current Spec (Agreed So Far)

Trigger:
- If intent routing is enabled AND transcript begins with a configured wake prefix (default `zwingli`).
- “Not word boundary strict”: `zwingli:` and `zwingliWhatever` both trigger.
- If intent routing is disabled, no prefix scan occurs and the transcript is always treated as dictation.

Prefix stripping:
- Remove the wake prefix and then trim leading punctuation/whitespace.
- The prefix is not sent to the LLM.

LLM request:
- Messages:
  1. System prompt
  2. Optional user prompt (customizable)
  3. The spoken prompt (post-prefix)
- Output is typed/printed verbatim (multi-line allowed).
- Command-mode always implies an LLM call (no “command-but-no-LLM” mode), except when strict mode refuses output.

Errors:
- Errors are logged to stderr (and `voicepipe-fast` mirrors stderr to a log file in hotkey contexts).
- If error reporting is enabled (default), `voicepipe-fast toggle` attempts to type the error into the target window.
- The main CLI will type errors only when `--type` is used and error reporting is enabled.

JSON output behavior:
- Current:
  - CLI: when `--json` is used, stdout is always exactly one JSON object on both success and failure (and exit code is non-zero on failure).
  - Fast path: when `VOICEPIPE_FAST_JSON=1` is set, `voicepipe-fast stop` prints a JSON payload on both success and common failure modes (including IPC failures).
- Target contract:
  - When JSON output is enabled, stdout is **always** exactly one JSON object (even on failures), containing at least `ok`, `stage`, and `error`, and the process exits non-zero on failure.
  - Human-readable text belongs on stderr.
  - Typing still happens when requested (e.g. `--type`), even in JSON mode; stdout remains JSON-only.

Recommended top-level JSON keys (for debugging + integration):
- `ok`: `true|false`
- `stage`: the failed stage when `ok=false` (best-effort; e.g. `transcribe|intent|zwingli|type|internal`)
- `error`: string (only when `ok=false`)
- `timing`: object with per-stage `*_ms` keys when available cheaply
- `intent`: always present (stable shape)
- `output_text`: string on success; may be `null` when unavailable on failures
- `zwingli`: present only when intent resolves to command-mode (object with provider diagnostics)

Timing:
- A best-effort `timing` object in JSON is acceptable when the timings are already available cheaply (no extra heavy instrumentation).

## 8) Planned Work (Not Implemented Yet)

These are intentionally **not** implemented yet; they’re the next concrete architecture steps.

1) Centralize “error reporting” in one helper
- Currently CLI + fast path do similar error-typing logic in different places.
- A shared helper would reduce drift.

2) Consider a zwingli daemon (latency + reliability)
- Today zwingli processing is in-process (adds startup + SDK init latency).
- A small persistent “zwingli daemon” (similar to the transcriber daemon) could:
  - keep the LLM client warm
  - centralize error handling + JSON error payloads
  - make hotkey workflows feel more consistent

## 9) Confirmed Decisions (So Far)

1) Zwingli mode scope
- When enabled, zwingli mode applies consistently across:
  - `voicepipe stop`, `voicepipe dictate`, `voicepipe transcribe-file`
  - `voicepipe-fast stop`, `voicepipe-fast toggle`

2) Error typing scope
- For the non-fast CLI, Voicepipe types errors only when `--type` is set (and error reporting is enabled).

3) Empty LLM output
- Empty zwingli output is treated as an error (not a no-op).

4) Shared post-STT pipeline
- Implemented in `voicepipe/pipeline.py` and used by both the CLI and `voicepipe-fast`.

5) Zwingli daemon for lower latency
- Add a persistent zwingli daemon to reduce latency vs. in-process LLM calls.

6) Structured JSON on failure
- When JSON output is enabled (`--json` or `VOICEPIPE_FAST_JSON=1`), failures emit a structured JSON payload (including at least `ok`, `stage`, and `error`) and exit non-zero.

7) JSON stdout contract
- When JSON output is enabled, stdout is JSON only; any human text goes to stderr.

8) Zwingli daemon behavior
- Prefer zwingli daemon when available, but fall back to in-process zwingli for non-systemd / non-daemon setups (mirrors the recorder/transcriber philosophy).

9) systemd integration for best performance
- Add a `voicepipe-zwingli.service` and include it in `voicepipe.target` by default for best hotkey performance.
- Zwingli daemon IPC can be simple request/response JSON over a Unix socket (no streaming required).

10) Zwingli JSON metadata
- JSON outputs should include a `zwingli` object when zwingli mode is used.

11) Zwingli backend independence
- Zwingli processing must have its own configurable backend (most likely Groq).

12) Groq credentials
- Use `GROQ_API_KEY` (not a namespaced var).

13) Default zwingli backend
- Default zwingli backend is Groq.

14) Zwingli JSON diagnostics
- The JSON `zwingli` object should include any useful diagnostic data available from the provider (e.g. backend, model, timings, request id, usage, errors), without requiring scraping stderr logs.

15) Default zwingli model
- Default Groq model for zwingli should be `moonshotai/kimi-k2-instruct` (and remain configurable).

16) Groq API style
- Prefer Groq’s OpenAI-compatible API (so we can reuse the existing `openai` Python SDK) instead of adding a separate Groq SDK.

17) Groq base URL config
- Store the Groq/OpenAI-compatible base URL (`https://api.groq.com/openai/v1`) in `config.toml` (settings), not as an environment variable.

18) JSON schema + prompts + timing
- Keep JSON payload mostly flat (with `intent`, `output_text`, and a `zwingli` object).
- Do not include prompts in JSON (or prompt hashes) by default.
- Include per-stage timing only when already available cheaply.

19) JSON ok + stage
- Prefer stable top-level `ok` and `stage` fields in JSON outputs to support integration and easier debugging.
- `stage` should represent the failed stage when `ok=false`.

## 10) Open Questions (Keep Small)

1) JSON typing metadata
- Should JSON include typing metadata (e.g. `type_requested`, `typed_ok`, `typed_backend`), or keep typing out of JSON entirely?

2) `stage` on success
- On `ok=true`, should `stage` be omitted, or set to a constant like `"complete"`? (Current: `stage="complete"`.)
