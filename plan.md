# VoicePipe Project Plan (Zwingli / Transcript Commands)

VoicePipe is two things:
1) A fast, reliable **voice dictation** tool (record → transcribe → print/type).
2) A *post-transcription* command layer that can be activated by a **master trigger word** (initially: `zwingli`) to run additional logic on the transcript (LLM rewrite, bash generation, command execution, etc.).

This document describes the purpose of the overall project and a step-by-step plan to evolve the Zwingli command architecture **incrementally** while preserving VoicePipe’s core quality: **dictation must stay fast and predictable when no trigger word is present**.

---

## Purpose (What We’re Building)

### Primary product goal
Speak into the mic, and get correct text into the focused application with minimal friction.

### Secondary product goal (Zwingli)
When the transcript begins with a configured trigger word, treat the transcript as an instruction to:
- run an LLM preprocessor (rewrite / generate)
- or execute an explicit function (e.g. run a shell command)
- or do a combination (LLM routes to a function, then returns output)

### What this is NOT
- Not an always-listening audio wake word.
- Not a background agent that “does stuff” unless explicitly requested.

---

## Key constraints (non-negotiable)

1) **Passive “grep”-style detection**
   - Checking for triggers must be cheap: string prefix checks only.
   - No disk reads, no LLM calls, no subprocess work unless a trigger matches.

2) **Permanent storage, daemon-loaded**
   - Triggers/verbs/prompt profiles should be stored in a persistent config file.
   - The transcriber daemon should load this config once at startup (and/or explicit reload) and keep it in memory.

3) **Safe defaults**
   - Side effects (shell execution, sending email) must be **disabled by default**.
   - Mis-transcription is assumed; the system must avoid “oops I ran something destructive”.

4) **Observable**
   - `--json` output should be able to show what happened (trigger/verb/action, timing, errors).

---

## Glossary

- **STT**: speech-to-text (OpenAI / ElevenLabs).
- **Trigger word / prefix**: a prefix in the transcribed text (e.g. `zwingli …`) that activates postprocessing.
- **Master trigger**: a trigger word that gates access to subcommands (verbs).
- **Verb**: a “code word” only meaningful when preceded by the master trigger (e.g. `execute`, `bash`, `rewrite`, `email`).
- **Handler**: code that implements a verb.
- **Daemon**: long-lived process that keeps expensive state warm and avoids repeated cold starts.

---

## Current baseline (what we have today)

We already have enough plumbing to prove the concept:
- Post-transcription triggers exist and are applied after STT.
- Triggers are currently configured via env vars (e.g. `VOICEPIPE_TRANSCRIPT_TRIGGERS=…`).
- A transcriber daemon exists and loads trigger config at startup.
- There are built-in postprocessing actions like:
  - `strip`: remove trigger prefix and output remainder
  - `zwingli`: pass remainder to an LLM and output the result
  - `shell`: execute remainder in a shell (gated behind explicit enablement)

This baseline is useful for testing and for proving the “grep” performance property, but **we want to replace env-var trigger configuration with a real config file**.

---

## Target user experience (what you can say)

Dictation (no trigger word; nothing special happens):
- `ps aux | grep -v codex` → output exactly that text

Master trigger present (safe default behavior; no side effects):
- `zwingli ps aux | grep -v codex` → output `ps aux | grep -v codex` (strip only)

Master trigger + explicit verb:
- `zwingli rewrite can you make this more formal …` → output rewritten text
- `zwingli bash find big files in my home directory` → output a bash script (not executed)
- `zwingli execute ps aux | grep -v codex` → execute shell command (ONLY if explicitly enabled)
- `zwingli email kelly subject when is lunch message i hope you're doing well …` → output an email draft (not sent)

The key idea is: **verbs are only recognized when the master trigger word is present**.

---

## Proposed architecture (text pipeline)

### High-level data flow
1) Record audio (daemon/subprocess) → audio bytes / WAV path
2) Transcribe (prefer transcriber daemon) → transcript text + STT metadata
3) Trigger detection (cheap string checks)
   - If no trigger: return transcript unchanged
   - If trigger: strip trigger → remainder
4) Dispatch (only if configured)
   - Parse remainder as `verb + args`
   - If verb is known and enabled: run handler
   - Else: safe fallback behavior (default: strip only)
5) Output (print and/or type) + structured artifact (`--json`)

### Performance note
Trigger detection can be effectively O(1) by extracting the first token (or first token before `, : ; .`) and comparing against a set of triggers.

---

## Configuration plan (file-based, daemon-loaded)

### Why a config file
Env vars are OK for secrets and simple toggles, but are a poor fit for:
- structured mappings (trigger → action, verb → handler)
- per-verb settings (timeouts, allowlists, model prompts, etc.)
- editing and reviewing changes

### Where it lives (cross-platform)
Store this alongside the existing VoicePipe config directory:
- `config_dir()/triggers.json` (or `commands.json`)

Examples:
- Linux: `~/.config/voicepipe/triggers.json`
- macOS: `~/Library/Application Support/voicepipe/triggers.json`
- Windows: `%APPDATA%\\voicepipe\\triggers.json`

### Config format
Start with JSON (stdlib) and include `version` for migrations.

### Proposed schema v1 (draft)
```jsonc
{
  "version": 1,

  "triggers": {
    // Master trigger(s) and common mis-transcriptions as aliases:
    "zwingli": { "action": "dispatch" },
    "zwingly": { "action": "dispatch" }
  },

  "dispatch": {
    // If we don’t recognize the first token after zwingli, what do we do?
    // Recommended default: output the remainder unchanged.
    "unknown_verb": "strip"
  },

  "verbs": {
    // Safe verbs (no side effects)
    "strip":   { "type": "builtin" },
    "rewrite": { "type": "llm", "profile": "rewrite" },
    "bash":    { "type": "llm", "profile": "bash" },
    "email":   { "type": "llm", "profile": "email_draft" },

    // Unsafe verbs (side effects) MUST be explicitly enabled
    "execute": { "type": "shell", "enabled": false, "timeout_seconds": 10 }
  },

  "llm_profiles": {
    "rewrite": {
      "model": "gpt-5.2",
      "temperature": 0.2,
      "system_prompt": "You are a dictation preprocessor. Output only the final text to type."
    },
    "bash": {
      "model": "gpt-5.2",
      "temperature": 0.2,
      "system_prompt": "Write a bash script. Output only the script.",
      "user_prompt_template": "Write a bash script based on this phrase: {{text}}"
    },
    "email_draft": {
      "model": "gpt-5.2",
      "temperature": 0.2,
      "system_prompt": "Draft an email. Output only: To, Subject, Body."
    }
  }
}
```

### Secrets & credentials
- Keep API keys in `voicepipe.env` (systemd-friendly, existing tooling).
- Keep non-secret behavior (triggers/verbs/prompts) in `triggers.json`.

### Daemon loading rules
- On daemon start: read + validate config once; store the parsed config in memory.
- No per-request disk reads.
- If config is changed, user either:
  - restarts daemon, or
  - sends a reload signal/command (later milestone).

---

## Parsing rules (how speech becomes a verb call)

### Trigger matching (boundary-aware)
We match `zwingli` only when it appears as a true prefix:
- `zwingli …` matches
- `zwingli, …` matches
- `zwingli: …` matches
- `zwinglix …` does NOT match

### Dispatch parsing
When a trigger action is `dispatch`:
1) Strip the trigger prefix → `remainder`
2) Split `remainder`:
   - `verb` = first token
   - `args` = everything after verb
3) If `verb` exists in config and is enabled:
   - run handler(verb, args)
4) Else:
   - follow `dispatch.unknown_verb` policy
     - recommended default: `strip` (return args unchanged; no side effects)

This design ensures `zwingli ps aux …` does NOT accidentally execute anything.

---

## Safety model (avoid catastrophes)

### Default behavior is safe
- If trigger not present: dictation only.
- If trigger present but verb unknown: strip only.
- Unsafe verbs like `execute` must be disabled by default.

### Recommended execution constraints when enabled
For `execute`:
- no stdin (`DEVNULL`)
- short timeout
- capture output
- return code + duration in metadata

### “Generate vs execute” separation
We should keep “generate a script” (`bash`) separate from “run a script” (`execute`) so the safe path remains the default.

---

## CLI / UX plan

Add dedicated commands for triggers config:
- `voicepipe triggers init` (writes a safe `triggers.json`)
- `voicepipe triggers edit` (opens in `$EDITOR`)
- `voicepipe triggers show` (prints resolved config, never secrets)
- `voicepipe doctor triggers` (shows what the daemon has loaded)

Add runtime toggles:
- `--no-triggers` (debug: bypass postprocessing)
- `--json` includes trigger/verb/action metadata when applied. (DONE: 2026-02-25)

---

## Testing plan

### Unit tests (default)
- Trigger boundary matching
- Dispatch parsing and unknown-verb policy
- Verb enable/disable behavior
- Shell handler mocked (no real execution)
- LLM handler mocked (no network)
- JSON config validation

### Live tests (opt-in)
- Keep sample WAVs as manual fixtures
- Gate anything requiring network/API keys behind `VOICEPIPE_LIVE_TESTS=1`

---

## Milestones (incremental delivery)

### Phase 1 — Add file-based triggers config (DONE: 2026-02-22)
- Implement `triggers.json` read/validate.
- Daemon loads once at startup; in-process path loads once per CLI run.
- Keep env-var mapping temporarily as an override for dev/debug.

### Phase 2 — Master trigger dispatcher (`zwingli <verb> …`) (DONE: 2026-02-22)
- Implement `dispatch` action + verb routing.
- Default unknown verb behavior: strip only.

### Phase 3 — Safe verbs via LLM profiles (DONE: 2026-02-22)
- Add `rewrite`, `bash`, `email` as “generate only” outputs.
- Keep side effects off.

### Phase 4 — Side effects (explicit enable) (DONE: 2026-02-22)
- Add `execute` verb, disabled by default.
- Add constraints (timeout, audit metadata).

### Phase 5 — Reload + future extensibility (DONE: 2026-02-22)
- Add daemon reload (SIGHUP or explicit IPC command). (DONE: 2026-02-22)
- Consider plugin mechanism for user-defined verbs once the core is stable. (DONE: 2026-02-22)
  - Add `type: "plugin"` verbs that call a user-defined Python callable (`plugin: {path|module, callable}`).
  - Keep safe defaults: plugin verbs default to disabled and require `VOICEPIPE_PLUGIN_ALLOW=1`; plugin files must live under the Voicepipe config dir.

### Phase 6 — Zwingli audio regression suite (IN PROGRESS: 2026-02-23)
- Add round 1 recorded audio fixtures + opt-in live tests (STT → trigger/dispatch; no side effects). (DONE: 2026-02-23)
  - Baseline transcription model: `gpt-4o-transcribe` (default).
  - Observed trigger mis-transcriptions in round 1 (use as optional alias triggers): `swingly`, `swing your`, `swing the`, `swing this trip`, `zwinglistrep`, `zwingle`.
- Add round 2 fixtures for separator/alias edge cases + disabled-verb semantics (enabled/disabled LLM verbs). (IN PROGRESS: 2026-02-23)
  - Live tests should use `gpt-4o-transcribe` for STT and `gpt-5.2` for LLM verbs.
  - Implemented round 2 live tests for:
    - Disabled LLM verbs → `disabled_verb` metadata + safe fallback output.
    - Enabled LLM verbs (gated by `VOICEPIPE_LIVE_LLM_TESTS=1`) → LLM dispatch + metadata invariants.
  - Added a separator-focused live test harness that will run once round 2 audio is recorded.
  - Separator-focused audio fixtures are still pending recording (see `tests/assets/zwingli_round2/manifest.json`).
- Add round 3 fixtures for `execute` enable path + timeout semantics (still gated; non-destructive commands only). (IN PROGRESS: 2026-02-23)
  - Added round 3 planned fixtures manifest: `tests/assets/zwingli_round3/manifest.json`.
  - Added round 3 live tests:
    - Disabled `execute` verb → `disabled_verb` metadata + safe fallback output.
    - Enabled `execute` verb but shell disallowed → `ok=false` + clear `VOICEPIPE_SHELL_ALLOW=1` error.
    - Enabled `execute` verb execution (gated by `VOICEPIPE_LIVE_SHELL_TESTS=1`) → shell dispatch + metadata invariants.
    - Timeout case harness added; `sleep 2` audio fixture still pending recording.
- Add round 4 fixtures for plugin verbs (pure transforms; no filesystem writes). (IN PROGRESS: 2026-02-25)
  - Added round 4 live tests for plugin verb gating + metadata invariants (reuses round 1 audio fixture). (DONE: 2026-02-25)
  - Added planned fixtures manifest: `tests/assets/zwingli_round4/manifest.json`. (DONE: 2026-02-25)

---

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

---

## Zwingli transcript-trigger preprocessing (manual fixtures)

Goal: keep transcript trigger handling **passive/lightweight** unless a trigger word is present (“just a grep”),
then optionally run extra LLM processing before the final destination (typing, etc).

### Fixtures (created 2026-02-21)

- `zwingli1.wav`: `https://tmp.uh-oh.wtf/2026/02/21/c809d90e-zwingli1.wav`
- `zwingli2.wav`: `https://tmp.uh-oh.wtf/2026/02/21/867b7c44-zwingli2.wav`
- `zwingli_advanced.wav`: `https://tmp.uh-oh.wtf/2026/02/21/9d7a7250-zwingli_advanced.wav`

### Suggested local workflow

- Download:
  - `mkdir -p /tmp/voicepipe_zwingli_tests && cd /tmp/voicepipe_zwingli_tests`
  - `curl -fsSL -o zwingli1.wav https://tmp.uh-oh.wtf/2026/02/21/c809d90e-zwingli1.wav`
  - `curl -fsSL -o zwingli2.wav https://tmp.uh-oh.wtf/2026/02/21/867b7c44-zwingli2.wav`
  - `curl -fsSL -o zwingli_advanced.wav https://tmp.uh-oh.wtf/2026/02/21/9d7a7250-zwingli_advanced.wav`
- Transcribe:
  - `poetry run voicepipe transcribe-file --json /tmp/voicepipe_zwingli_tests/zwingli1.wav`
  - `poetry run voicepipe transcribe-file --json /tmp/voicepipe_zwingli_tests/zwingli2.wav`
  - `poetry run voicepipe transcribe-file --json /tmp/voicepipe_zwingli_tests/zwingli_advanced.wav`
- If the trigger word is mis-transcribed, retry with a vocabulary hint:
  - `poetry run voicepipe transcribe-file --json --prompt "The speaker may start by saying the command word 'zwingly'." /tmp/voicepipe_zwingli_tests/zwingli2.wav`

### Expected behavior

- `zwingli1.wav`:
  - Transcription is normal dictation (no trigger).
  - `output_text == text`
  - `transcript_trigger` is absent.
- `zwingli2.wav`:
  - Transcription begins with `Zwingly, ...`
  - `transcript_trigger.action == "zwingli"`
  - `output_text` is the LLM-processed result (should not include the trigger word).
- `zwingli_advanced.wav` (example of “more processing”):
  - Transcription resembles: `Zwingly, execute this command: curl asdf.us/python.`
  - `output_text` is the extracted/cleaned command (e.g. `curl asdf.us/python`).

### Next refactor (after `zwingli1.wav` + `zwingli2.wav` are reliable)

- Make trigger words more reliably recognized by STT:
  - Add a config/default for a transcription prompt (e.g. `VOICEPIPE_TRANSCRIBE_PROMPT`). (DONE: 2026-02-25)
  - Support appending trigger-word hints to the STT prompt (opt-in via `VOICEPIPE_TRANSCRIBE_PROMPT_APPEND_TRIGGERS=1`). (DONE: 2026-02-25)
- Extend transcript triggers beyond “single pass text rewrite”:
  - Add per-verb `destination` hints (`print|clipboard|type`) surfaced in dispatch metadata / `--json` (and optionally respected when `VOICEPIPE_COMMANDS_RESPECT_DESTINATION=1`). (DONE: 2026-02-25)
  - Allow trigger actions to return structured output (e.g. `{destination: "type"|"clipboard"|"shell", text: ...}`).
  - Support multi-step LLM processing for advanced workflows (e.g. command extraction + safety pass) using `zwingli_advanced.wav` as the first regression fixture.
