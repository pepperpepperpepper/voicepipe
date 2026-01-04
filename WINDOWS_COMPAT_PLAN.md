# Windows Compatibility Plan (Voicepipe)

## Goals (Windows MVP)

- Windows 10/11 (no WSL): `voicepipe start|stop|cancel|status|dictate|transcribe-file` work reliably.
- Windows 10/11: `voicepipe-fast toggle` works as the hotkey target (no systemd; logs to a file).
- Windows 10/11: `--type` works with an open, fast typing backend.
- Windows 10/11: `voicepipe config …` + `voicepipe setup` use Windows-appropriate paths and messaging.
- Offline `pytest` suite passes on Windows CI by default (no mic/network/API keys required).

## Non-goals (initial)

- Always-listening audio capture (Voicepipe records only when the user initiates `start` / `toggle`).
- Shipping a Windows background service/daemon **by default** before measuring whether subprocess start latency is unacceptable.
- Supporting the existing Unix-socket recorder/transcriber daemons on Windows (until we add a supported Windows transport).
- Supporting Windows versions where required primitives are missing (target is Win10/11).

## Principles / Constraints

- **Performance-first**: no expensive detection/probing in hot paths (hotkeys).
- **No Linux regressions**: X11/Wayland paths must remain at least as fast and as reliable.
- Prefer **stdlib** on Windows unless a dependency clearly reduces bugs/maintenance.
- Keep behavior **predictable**: one “daemon policy” knob should govern all daemon attempts.
- **No console flicker on Windows hotkeys**: hotkey workflows should not spawn a visible console window (prefer a GUI entrypoint / `pythonw`-style launch).

## Notes from existing branches (useful, but we should improve)

There is an existing `feature/windows-compatibility` branch with prior work (PyAudio + `pywin32` named pipes, and a `pyautogui` typing attempt). That branch **predates the current `refactor` tree** (different module layout), so treat it as a reference for *concepts* and Windows failure modes, not as code we can cherry-pick cleanly. For the `refactor` tree, prefer:

- **No pywin32/pyautogui** in MVP (extra deps, more failure modes, worse perf); use `ctypes` (`SendInput`) for typing.
- If we later need a Windows daemon/IPC transport, prefer **stdlib** `multiprocessing.connection` (`AF_PIPE`) with JSON `send_bytes`/`recv_bytes` (no pickle).

## Known Windows pitfalls (design for these explicitly)

- **No shell RC / profile env**: hotkeys and Task Scheduler won’t see `setx`/PowerShell profile exports reliably; config must come from `voicepipe.env` (and `VOICEPIPE_ENV_FILE` must be honored).
- **No-console launches** (`pythonw`, GUI hotkey runners): `sys.stderr`/`sys.stdout` can be `None`; logging must not assume `.isatty()` or `.fileno()`.
- **UIPI / elevation**: a non-elevated process generally cannot inject keys into an elevated target window. We should surface a clear “can’t type into elevated app” hint in docs/error paths (best-effort; detection may be imperfect).
- **Locked / non-interactive sessions**: typing (SendInput) will be unreliable when the session is locked, or when the agent runs as a service. Desktop typing tests must account for this.

## 0) Audit: what is Windows-hostile in the current `refactor` tree

Concrete issues (source-aligned):

- `voicepipe/paths.py`
  - Uses `os.getuid()` (not available on Windows).
  - Assumes Linux runtime conventions (`/run/user/$UID`).
- Import-time path evaluation / creation (breaks `import voicepipe` on Windows):
  - `voicepipe/fast.py`: `TMP_DIR = runtime_app_dir(create=True)` at import time.
  - `voicepipe/session.py`: `RecordingSession.STATE_DIR = session_state_dir()` at import time.
  - `voicepipe/daemon.py`: `RecordingDaemon.SOCKET_PATH = daemon_socket_path()` at import time.
- Import-time side effects beyond paths:
  - `voicepipe/fast.py`: opens a log file and `dup2()`s stderr at import time (bad for Windows + unit tests; also problematic under `pythonw` where stderr may not have a `fileno()`).
  - `voicepipe/fast.py`: assumes `sys.stderr` is always usable (calls `sys.stderr.isatty()` and prints to it); under `pythonw` / GUI hotkey launches on Windows, `sys.stderr` (and `sys.stdout`) may be `None`, which would crash.
- `voicepipe/config.py`
  - `config_home()` hardcodes `Path.home() / ".config"` (reasonable for Linux, wrong default for Windows).
- `voicepipe/session.py`
  - Uses `os.kill(pid, 0)` as a liveness check (semantics differ on Windows; also needs to treat EPERM as “running” on Unix).
- Subprocess recording stop/cancel is Unix-signal driven:
  - `voicepipe/recording_subprocess.py` uses `signal.pause()` (Unix-only).
  - `voicepipe/recording_backend.py` uses `os.kill(pid, SIGTERM|SIGINT)` which is not a safe “stop & flush” contract on Windows.
- Daemon IPC is Unix-domain-socket based:
  - `voicepipe/ipc.py` and `voicepipe/transcription.py` assume `socket.AF_UNIX` works (it often does on modern Windows, but we should not require it for MVP).
- Some diagnostics paths are POSIX-centric:
  - `voicepipe/commands/doctor.py` uses POSIX-only `start_new_session=True` and `os.killpg` for ffplay process-group cleanup (will raise on Windows unless guarded).
- Config + tests assume Unix permission semantics:
  - `voicepipe/config.py` + tests assume `chmod 0600` is meaningful (`env_file_permissions_ok`).
  - `tests/test_session.py` asserts `0o600/0o700` modes (will fail on Windows).
- Config UX is Linux-first:
  - `voicepipe/commands/config.py` hardcodes `~/.config/...` in docstrings and prints systemd restart instructions (all subcommands: `set-openai-key`, `set-elevenlabs-key`, `edit`, `migrate`).
  - `voicepipe config edit` falls back to `nano|vim|vi` (Windows should default to `notepad` or use `os.startfile()`).
  - `voicepipe/commands/recording.py` + `voicepipe/commands/smoke.py` help strings hardcode Linux typing tools (“xdotool/wtype”).
- Atomic file replace semantics differ:
  - `voicepipe/config.py` uses `os.replace()` for env-file writes; on Windows this can fail when the target file is held open by another process (common if the user has `voicepipe.env` open in an editor). We need a Windows-friendly fallback strategy.
- Tests that spawn fake systemd tools won’t execute on Windows:
  - `tests/conftest.py` writes shebang scripts `systemctl`/`journalctl` (not runnable by `CreateProcess`) and prepends to `PATH` using `:` instead of `os.pathsep`.
  - `tests/test_cli_config.py` writes a `#!/bin/sh` editor stub (not runnable by `CreateProcess`).
- Typing is Linux-only today:
  - `voicepipe/typing.py` only supports `wtype`/`xdotool`.
- `sounddevice` import robustness:
  - `voicepipe/recorder.py` and `voicepipe/daemon.py` only catch `ImportError`, but on Windows (and some misconfigured installs) `import sounddevice` can raise `OSError` if PortAudio cannot be loaded. We should treat that as “dependency unavailable” and surface a friendly, actionable error instead of crashing at import time.

## 1) Strategy: Windows MVP = daemonless by default

### Default behavior on Windows (MVP)

- Recording uses the existing **subprocess backend**.
- Stop/cancel uses a **cross-platform control channel** (not Unix signals).
- Transcription runs in-process (no transcriber daemon required by default).

Rationale: end-to-end latency is dominated by STT network time; only add a daemon if we can show local start latency matters.

### One daemon policy knob (must govern all daemon attempts)

Introduce one env var respected everywhere:

- `VOICEPIPE_DAEMON_MODE=auto|never|always`
  - `auto`:
    - POSIX (Linux/macOS): attempt daemons when sockets exist (current behavior).
    - Windows: behave like `never` until we add a supported transport (or explicitly enable a Windows transport).
  - `never`: skip daemon checks entirely (fastest + quietest).
  - `always`: require daemon; fail fast with actionable guidance.

This must be applied to BOTH recorder daemon usage (IPC) and transcriber daemon usage (STT over socket).

## 2) Implementation plan (phased)

### Phase A — Platform helpers + directory model (unblocks imports)

Goal: `import voicepipe`, `voicepipe --help`, and offline `pytest` can run on Windows without crashing.

Tasks:
- [ ] Add `voicepipe/platform.py`:
  - [ ] `is_windows()`, `is_linux()`, `is_macos()`.
  - [ ] `pid_is_running(pid)`:
    - Windows: `ctypes` `OpenProcess` + `GetExitCodeProcess`.
    - Unix: `os.kill(pid, 0)` but treat `PermissionError` as “running”.
  - [ ] (Optional) `supports_af_unix()` (used for tests/guardrails), but do not make it a hard dependency for MVP.
- [ ] Make optional dependency imports robust on Windows:
  - [ ] `voicepipe/recorder.py` and `voicepipe/daemon.py`: treat any exception importing `sounddevice` as “unavailable” (align with `voicepipe/audio.py`) so missing PortAudio DLLs don’t crash `import voicepipe` / `voicepipe start`.
- [ ] Refactor `voicepipe/paths.py` to be OS-aware (and avoid calling `os.getuid()` on Windows):
  - [ ] Keep Linux behavior: `XDG_RUNTIME_DIR` → `/run/user/$UID` → temp fallback.
  - [ ] Windows runtime dir (sessions/temp audio/sockets):
    - default: `%TEMP%\\voicepipe` (i.e. `tempfile.gettempdir()\\voicepipe`; short + per-user + intended for ephemeral artifacts)
    - fallback: `%LOCALAPPDATA%\\voicepipe\\run` (for restricted/odd `%TEMP%` environments)
  - [ ] Windows persistent state:
    - default: `%LOCALAPPDATA%\\voicepipe\\state`
  - [ ] Windows logs dir:
    - default: `%LOCALAPPDATA%\\voicepipe\\logs`
  - [ ] Windows fallbacks must be robust:
    - If `APPDATA`/`LOCALAPPDATA` are missing (rare, but happens in restricted shells/services), fall back to `Path.home()` and then `tempfile.gettempdir()`.
    - Avoid AF_UNIX path-length surprises by keeping any future Windows IPC endpoint paths short (prefer `%TEMP%` or Named Pipes for IPC).
  - [ ] Add an explicit `logs_dir()` helper (used by `voicepipe-fast` on Windows), rather than inventing ad-hoc locations in individual modules.
  - [ ] Ensure config/state/log/runtime path “sources of truth” are not duplicated across modules (either keep config paths in `voicepipe/config.py` or centralize in `voicepipe/paths.py`, but avoid scattered strings).
  - [ ] Preserve existing XDG behavior on Linux (`XDG_STATE_HOME`, etc).
- [ ] Remove import-time path evaluation/creation:
  - [ ] `voicepipe/fast.py`: compute runtime dir lazily (no module-level `TMP_DIR = …create=True`), and make the module import-safe on Windows:
    - no unconditional `import fcntl`
    - no `/proc` access (e.g., fluxbox detection) at import time (guard Linux-only behavior)
    - no `sys.stderr`/`sys.stdout` access at import time (under `pythonw` they may be `None`)
    - delay log setup until `main()`/`execute_toggle()` (no `dup2()` at import time)
  - [ ] `voicepipe/session.py`: compute state dir lazily (no class attr calling `session_state_dir()` at import).
  - [ ] `voicepipe/daemon.py`: compute socket path in `__init__` or `start()` (no class attr calling `daemon_socket_path()` at import).
- [ ] Improve Windows diagnostics:
  - [ ] `voicepipe/commands/doctor.py` (`doctor env`): print Windows-relevant env vars (`APPDATA`, `LOCALAPPDATA`, `TEMP`, `USERPROFILE`) and the resolved config/state/log/runtime dirs (without creating them).

Acceptance:
- On Windows: `python -c "import voicepipe"` works.
- On Windows: `python -c "import voicepipe.fast"` works (no `fcntl`/`/proc` import-time crash).
- On Windows: `voicepipe doctor env` prints sane computed dirs and does not crash (even if systemd / X11/Wayland env vars are absent).

### Phase B — Config paths + permissions semantics (Windows-normal)

Goal: env-file configuration works on Windows without confusing Linux-only messaging.

Tasks:
- [ ] `voicepipe/config.py`:
  - [ ] Support `VOICEPIPE_ENV_FILE` override (absolute path wins everywhere).
  - [ ] Windows default env file: `%APPDATA%\\voicepipe\\voicepipe.env` (create parent dir as needed).
    - If `%APPDATA%` is missing, fall back to `%LOCALAPPDATA%\\voicepipe\\voicepipe.env`, then `Path.home()\\AppData\\Roaming\\voicepipe\\voicepipe.env`, then finally `%TEMP%\\voicepipe\\voicepipe.env`.
  - [ ] Keep Linux default stable for systemd: `~/.config/voicepipe/voicepipe.env`.
  - [ ] Update docstring/help text that currently hardcodes `~/.config/voicepipe/voicepipe.env`.
  - [ ] `env_file_permissions_ok()`:
    - Windows: return `None` (“not applicable”).
    - Unix: keep existing 0600 check.
  - [ ] `ensure_env_file()` / `upsert_env_var()`:
    - Keep `chmod` best-effort, but do not *warn* about permissions on Windows (treat as informational only).
    - Ensure writes are CRLF-tolerant (dotenv parser already is) and do not introduce `\r` into values.
    - [ ] Make env-file writes robust on Windows:
      - Keep `os.replace()` for atomicity when it works.
      - If replace fails (e.g., file is open/locked), fall back to an in-place write with a clear warning (avoid bricking `voicepipe config set-*` while the user is editing the file).
  - [ ] Update `DEFAULT_ENV_FILE_TEMPLATE` comments:
    - include Windows typing backends (e.g. `sendinput`) once implemented.
    - include `VOICEPIPE_DAEMON_MODE=auto|never|always` (so users have a single knob to force/disable daemon attempts).
- [ ] Update CLI help / user messaging to avoid hardcoding `~/.config/...` and avoid assuming systemd on Windows:
  - [ ] `voicepipe/commands/config.py`:
    - [ ] Update docstrings to reference the resolved env file path (via `env_file_hint()`).
    - [ ] Replace “restart systemd services” guidance with OS-aware guidance:
      - Linux (systemctl available): keep current systemd restart hints.
      - Windows: never mention systemd; suggest re-running `voicepipe` / re-launching hotkey app, and point to Task Scheduler/Startup folder for auto-start.
      - Prefer implementing this once as a helper (e.g. `voicepipe/commands/_hints.py: print_restart_hint()`) and calling it from *all* relevant subcommands (`set-openai-key`, `set-elevenlabs-key`, `edit`, `migrate`).
    - [ ] `config edit` (Windows): when `$EDITOR` is missing, default to `notepad` (blocking, predictable). Only fall back to `os.startfile(env_path)` if launching `notepad` fails.
  - [ ] `voicepipe setup` messaging: already skips when `systemctl` missing; keep but make Windows messaging more explicit (Task Scheduler / Startup folder).

Acceptance:
- On Windows: `voicepipe config show` reports the resolved env file path and does not warn about 0600.

### Phase C — Daemon policy plumbing (`VOICEPIPE_DAEMON_MODE`) (perf + correctness)

Goal: Windows doesn’t even try Unix-socket daemon paths unless explicitly requested.

Tasks:
- [ ] Add a small helper (either `voicepipe/config.py` or `voicepipe/platform.py`) to parse daemon mode:
  - `get_daemon_mode() -> Literal["auto","never","always"]` with strict validation.
- [ ] Apply daemon mode consistently:
  - [ ] `voicepipe/recording_backend.py` (`AutoRecorderBackend`):
    - If mode is `never`, never call daemon.
    - If mode is `auto` and platform is Windows, never call daemon.
    - If mode is `always`, do not fall back silently; surface actionable errors.
  - [ ] `voicepipe/transcription.py`:
    - If mode is `never`, treat `prefer_daemon=False` even if the caller passes True.
    - If mode is `auto` on Windows, treat `prefer_daemon=False`.
  - [ ] `voicepipe/fast.py`:
    - Respect daemon mode and avoid printing `systemctl` hints on Windows.
  - [ ] Harden daemon IPC calls on Windows even when enabled:
    - [ ] `voicepipe/ipc.py`: if `socket.AF_UNIX` is unsupported at runtime, raise `IpcUnavailable` (so callers fall back cleanly).
    - [ ] `voicepipe/transcription.py`: same for `_transcribe_via_daemon` (treat as `TranscriberDaemonUnavailable`).
  - [ ] Make “daemon entrypoints” explicit on Windows (MVP is daemonless):
    - [ ] `voicepipe/commands/recording.py` (`voicepipe daemon`): on Windows, fail fast with an actionable message (“daemon mode is not supported on Windows yet; use `voicepipe start/stop` or `voicepipe-fast toggle`”).
    - [ ] `voicepipe/transcriber_daemon.py` (`voicepipe-transcriber-daemon`): same (or guard inside `main()`), so users don’t hit cryptic `AF_UNIX` errors.

Acceptance:
- On Windows: `voicepipe status` does not scan for sockets and does not emit daemon guidance.
- On Linux: default behavior remains unchanged unless user sets `VOICEPIPE_DAEMON_MODE`.

### Phase D — Fix session tracking liveness on Windows (required)

Goal: stale sessions are cleaned up correctly and don’t wedge the CLI.

Tasks:
- [ ] `voicepipe/session.py`:
  - [ ] Replace `_is_process_running` with `platform.pid_is_running`.
  - [ ] Make directory selection lazy (from Phase A).

Acceptance:
- On Windows: repeated `voicepipe start` behaves correctly (only rejects when the recording subprocess truly exists).

### Phase E — Cross-platform subprocess stop/cancel (required)

Goal: subprocess recording can be stopped/cancelled without Unix signals.

Design (minimal, robust, low overhead):
- Session JSON includes a `control_path`.
- `voicepipe stop|cancel` writes `stop`/`cancel` into that file.
- Recorder subprocess polls at a small interval (e.g. 50–100ms) using `Event.wait(timeout=…)`.
  - Optimization: check `stat().st_mtime_ns` and only re-read the file when it changes (avoids constant disk I/O while recording).
  - Note: avoid `os.replace()` for the control file on Windows (cannot replace files that are open elsewhere); prefer truncating writes and treat empty/partial reads as “no command yet”.
  - Robustness: `stop` should not return until the recorder has flushed the audio file to disk (e.g., wait for process exit + audio file size to be stable/non-zero for 1–2 polls).

Tasks:
- [ ] `voicepipe/session.py`:
  - [ ] Include `control_path` in the session JSON.
- [ ] `voicepipe/recording_subprocess.py`:
  - [ ] Remove `signal.pause()` and implement the control loop.
  - [ ] Keep Linux signals only as an optional optimization; control channel is the contract.
  - [ ] Timeout should trigger the same stop path (no self-`os.kill`).
- [ ] `voicepipe/recording_backend.py`:
  - [ ] `start()`:
    - Replace fixed `sleep(0.5)` with polling for “session file exists + has control_path” (Windows spawn can be slower, and antivirus can add jitter).
    - Optional: read session JSON to return `recording_id` reliably (and in the future, allow returning `audio_file` too).
  - [ ] `stop()` / `cancel()` write to `control_path` and wait for graceful exit (poll `pid_is_running`, timeouts + helpful errors).
  - [ ] Windows: spawn the recording subprocess without console flicker (e.g. `creationflags=subprocess.CREATE_NO_WINDOW`), since `voicepipe-fast` hotkey workflows may run without a console.

Acceptance:
- On Windows: `voicepipe start` → `voicepipe stop` produces an audio file (and the transcript if API key is configured).
- On Windows: `voicepipe cancel` stops reliably with no leftover session files.

### Phase F — Make `voicepipe-fast` cross-platform (required for Windows hotkeys)

Goal: `voicepipe-fast toggle` works on Windows without daemons and without Linux-only imports.

Tasks:
- [ ] Create `voicepipe/locks.py` (cross-platform, low overhead):
  - [ ] Prefer a PID-file lock implemented via atomic create (`os.open(..., O_CREAT|O_EXCL)`), so we avoid importing `fcntl`/`msvcrt` in hot paths.
  - [ ] Keep the lock FD open for the duration of the command (Windows prevents deletion while open; also reduces races).
  - [ ] Stale lock recovery: if lock exists, read PID and use `platform.pid_is_running` to decide whether it’s stale; if stale, remove + retry.
  - [ ] Keep the implementation tiny: used only for `voicepipe-fast toggle` to prevent concurrent executions.
- [ ] `voicepipe/fast.py`:
  - [ ] Remove unconditional `import fcntl` and `/proc` probing at import time; guard Linux-only behavior behind `platform.is_linux()` (or remove entirely).
  - [ ] Ensure any parent-process heuristics (Fluxbox detection) are Linux-only and never touch `/proc` on Windows.
  - [ ] Ensure log path resolution has no import-time filesystem side effects (important for Windows `import voicepipe.fast` + unit tests).
  - [ ] Replace `dup2()`-based stderr mirroring with an explicit file logger that works under `pythonw` (where `sys.stderr` may not have a real fd).
  - [ ] Make “no console” hotkey launches robust:
    - Under `pythonw`/GUI entrypoints, `sys.stderr` and/or `sys.stdout` can be `None`; avoid `print(..., file=sys.stderr)` and any `.isatty()` calls on missing streams.
    - Prefer a `fast_log()` helper that always writes to the log file and *optionally* mirrors to stderr only when it exists.
  - [ ] Reduce hotkey latency on Windows:
    - Keep `voicepipe-fast start` on a “thin import” path (lazy-import transcription/OpenAI clients only on the stop/transcribe path).
    - Avoid any subprocess probes / binary discovery (`which`) unless we’re actually going to type/transcribe in that invocation.
  - [ ] Replace direct IPC usage with `AutoRecorderBackend` so Windows uses subprocess control.
  - [ ] Logging:
    - [ ] Windows: default log file under the Windows logs dir (`paths.logs_dir()`).
    - [ ] Unix: keep the existing fast path default (runtime dir) to avoid performance regressions or unexpected disk I/O on hotkey invocation.
    - [ ] Allow overrides (`VOICEPIPE_FAST_LOG_FILE` or a unified `VOICEPIPE_LOG_FILE`).
  - [ ] Windows UX: provide a no-console launch path for hotkeys
    - Prefer adding a GUI-style entrypoint (so a packaged `voicepipe-fastw.exe` doesn’t create a console window).
    - If tooling makes that hard, document the recommended `pythonw -m voicepipe.fast toggle` invocation and an AutoHotkey example using `Run ... , , "Hide"`.
  - [ ] Update `tests/test_fast.py` for the refactor:
    - stop asserting “IPC send_request called”; `voicepipe-fast` should use `AutoRecorderBackend` on Windows and respect `VOICEPIPE_DAEMON_MODE` everywhere.
    - keep a unit test that `--help`/unknown commands behave correctly (works on all OSes).
    - add a unit test that simulates `pythonw` by setting `sys.stderr = None` (and optionally `sys.stdout = None`) and asserts `voicepipe.fast.main([...])` does not crash (logs instead).

Acceptance:
- On Windows: `voicepipe-fast toggle` starts recording; second invocation stops + transcribes.

### Phase G — Windows typing backend (open + fast)

Goal: `--type` works on Windows with no paid tooling.

Backends:
- Default Windows backend: `sendinput` via `ctypes` (Unicode-safe).
- Optional: `paste` backend (clipboard + Ctrl+V) for very large text; opt-in because it can disturb clipboard state.

Tasks:
- [ ] `voicepipe/typing.py`:
  - [ ] Add Windows backend(s) to `TypingBackendName` and selection logic (`sendinput`, optionally `paste`).
  - [ ] On `win32`, `auto` selects `sendinput` and *skips* X11/Wayland detection entirely (fast path).
  - [ ] Implement `sendinput` with `KEYEVENTF_UNICODE` and batch multiple `INPUT` structs per call for speed (avoid one syscall per character).
  - [ ] Newlines: normalize `\\n` to Windows-friendly keystrokes (`\\r` / VK_RETURN) so multiline dictation behaves predictably.
  - [ ] Add a Windows `get_active_window_id()` implementation (returning an `HWND` string) so `voicepipe-fast` can optionally attempt to re-focus the target window before typing (best-effort; keep this zero-cost when unused).
  - [ ] Make failures actionable:
    - If no interactive desktop session is available (common in “run as service” contexts), return a clear error from `type_text` rather than pretending typing succeeded.
  - [ ] Keep per-process caching so selection is effectively free.
  - [ ] Update tests for the new platform-based selection:
    - [ ] `tests/test_typing.py`: mark Linux display-tool selection tests as `skipif(sys.platform == "win32")` (because Windows `auto` should choose `sendinput`, not xdotool/wtype, even if a test sets `XDG_SESSION_TYPE`).
    - [ ] Add Windows-only unit tests that do **not** inject keys, but validate backend resolution + error paths.
- [ ] Update `--type` help strings to be cross-platform:
  - [ ] `voicepipe/commands/recording.py` and `voicepipe/commands/smoke.py`: stop hardcoding xdotool/wtype in help text; describe behavior in terms of “configured typing backend”.

Acceptance:
- On Windows: `voicepipe transcribe-file --type voicepipe/assets/test.mp3` injects text into the focused app (best-effort).

### Phase H — Windows “run at login” story (docs-first)

Goal: Windows users can run Voicepipe manually or at login without systemd.

Tasks:
- [ ] `README.md`:
  - [ ] Add a Windows section (Win10/11, no WSL) covering:
    - install (pip/pipx), and any audio prerequisites for `sounddevice`/PortAudio on Windows
    - config file location on Windows (`%APPDATA%\\voicepipe\\voicepipe.env` by default; mention `VOICEPIPE_ENV_FILE`)
    - typing backend behavior (`sendinput` default; `--type` requires an interactive desktop)
    - daemon note: default is daemonless on Windows; explain `VOICEPIPE_DAEMON_MODE`
  - [ ] AutoHotkey example binding to `voicepipe-fast toggle`.
    - Include a version that runs hidden (no console), if available.
  - [ ] Task Scheduler example (start at login).
  - [ ] Startup folder shortcut example.
- [ ] On Windows, `voicepipe service …` should fail with an actionable message (“systemd is not available; use Task Scheduler/Startup folder”) instead of only “systemctl not found”.
- [ ] Make `voicepipe doctor daemon --play` portable:
  - [ ] `voicepipe/commands/doctor.py`: avoid `start_new_session=True` on Windows (use `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` if we still need process-group semantics).
  - [ ] `voicepipe/commands/doctor.py`: prefer `proc.terminate()`/`proc.kill()` on Windows rather than `os.killpg`.
- [ ] Optional: add `install.ps1` only if it materially improves onboarding without adding fragile deps.

### Phase I — Tests + CI (required to keep it working)

Goal: Windows support is continuously enforced.

Tasks:
- [ ] Add CI matrix including:
  - [ ] `windows-latest` (GitHub-hosted Windows Server runner): unit tests + CLI tests that do not require an interactive desktop session.
  - [ ] `ubuntu-latest`: full default suite.
  - [ ] Optional: `self-hosted` Windows 11 “desktop” runner for UI/typing tests (see below).
- [ ] Add `pytest` marker declarations (to avoid `PytestUnknownMarkWarning`):
  - [ ] `desktop`: interactive desktop required
  - [ ] (Optional) `windows`: Windows-only behavioral tests (if it helps keep intent clear)
- [ ] Make tests Windows-aware:
  - [ ] `tests/conftest.py`: isolate Windows dirs by setting `USERPROFILE`, `HOMEDRIVE`/`HOMEPATH`, `APPDATA`, `LOCALAPPDATA`, `TEMP`, `TMP` (not just `HOME`), and use `os.pathsep` when prepending to `PATH`.
  - [ ] Fix/relax permission assertions:
    - [ ] `tests/test_session.py`: don’t assert `0o600/0o700` on Windows.
    - [ ] `tests/test_config.py`: update expectations for Windows default env path.
    - [ ] `tests/test_cli_config.py`: relax chmod assertions + editor stubs on Windows.
    - [ ] `tests/test_recording_backend.py`: update stop/cancel expectations to match the new control-channel contract (no SIGTERM/SIGINT on Windows).
  - [ ] Systemd/service tests:
    - [ ] Prefer skipping the “fake systemd” path on Windows and instead asserting Windows-specific behavior:
      - `voicepipe service …` returns an actionable error message.
      - `voicepipe setup` prints “systemctl not found; skipping systemd setup” (or improved Windows guidance).
    - [ ] Concretely update/guard these test modules:
      - `tests/test_cli_service.py` (Windows: assert actionable “systemd not available” error instead of executing fake systemd)
      - `tests/test_cli_setup.py` (Windows: still writes env file, but skips systemd installs/calls)
  - [ ] IPC tests using `socket.AF_UNIX`:
    - [ ] If `AF_UNIX` is not supported in the Windows runtime, skip the server/client socket tests.
    - [ ] Even if `AF_UNIX` exists, consider skipping AF_UNIX integration tests on Windows CI by default (we do not rely on it for MVP); keep them running on Linux CI to avoid regressions.
  - [ ] Update path tests to be OS-aware:
    - [ ] `tests/test_paths.py`: split into table-driven assertions for Linux vs Windows (Windows should validate `%LOCALAPPDATA%`/`%TEMP%`-based defaults and honor `XDG_*` overrides only on Unix-like platforms).
  - [ ] Add unit tests for:
    - directory selection (env file/state/runtime/log) across OSes (table-driven)
    - `VOICEPIPE_DAEMON_MODE` behavior
    - Windows typing backend resolution (no real key injection)
    - `pid_is_running` (mocked)
  - [ ] Add markers + jobs for “desktop-only” tests:
    - [ ] Add `pytest` marker `desktop` for tests that require an interactive session (SendInput, focus handling, etc).
    - [ ] Default CI (Windows Server) runs `pytest -m "not live and not desktop"`.
    - [ ] A self-hosted Windows 11 runner runs `pytest -m desktop` (optionally also `-m live` if API keys are present).
    - [ ] Document runner caveat: a Windows self-hosted runner installed as a **service** often cannot interact with the desktop; for SendInput tests it must run in an interactive user session.
    - [ ] Document “unlocked desktop” caveat: SendInput/foreground-window assertions are unreliable when the session is locked; the desktop runner should run under an auto-login user with an unlocked session during CI (or the tests should detect/skip when locked).
  - [ ] Desktop typing test design (self-hosted runner):
    - [ ] Prefer a controlled target app over Notepad:
      - Launch a minimal stdlib `tkinter` window that appends received keypresses to a temp file.
      - Bring it to foreground (ctypes `SetForegroundWindow`) and run `type_text(...)`.
      - Assert the temp file contains the expected text.
    - [ ] Mark this test `@pytest.mark.desktop` and `skipif(not win32)`.
  - [ ] Add GitHub Actions workflows:
    - [ ] Add `.github/workflows/tests.yml`:
      - [ ] Use a small Python version matrix (at least 3.9 + latest supported, e.g. 3.12) to catch Windows-only stdlib/ctypes issues early.
      - [ ] Install via pip (avoid Poetry in CI unless needed): `python -m pip install -U pip` then `python -m pip install .` then `python -m pip install pytest`.
      - [ ] Windows job: set `PYTHONUTF8=1` (or `PYTHONIOENCODING=utf-8`) to avoid Unicode surprises in subprocess output and logs.
      - [ ] `windows-latest` job runs `pytest -m "not live and not desktop"` (no interactive session assumptions).
      - [ ] `ubuntu-latest` job runs `pytest -m "not live"` (or match Windows filter).
    - [ ] Add `.github/workflows/desktop-tests.yml` (optional, self-hosted):
      - [ ] Runs on a labeled Windows 11 runner in an interactive user session.
      - [ ] Executes `pytest -m desktop` (and optionally `-m live` when secrets exist).
      - [ ] Prefer `workflow_dispatch` trigger (manual) so we don’t block PRs on a flaky GUI runner.

## 3) Performance checkpoints (measure before adding a daemon)

Add timing logs only when `--debug` or `VOICEPIPE_LOG_LEVEL=DEBUG`:

- Start latency: hotkey invocation → “recording active”.
- Stop latency: `stop`/`toggle` → “STT request sent”.
- Full end-to-end: `stop`/`toggle` → transcript printed/typed.

If Windows start latency is materially worse than Linux, add an OPTIONAL daemon phase:

- Transport: `multiprocessing.connection` `AF_PIPE`.
- Protocol: JSON via `send_bytes`/`recv_bytes` (avoid pickle).
- Security: include the current user identity in the pipe name (and/or a random handshake token) so other local users can’t control your recorder/transcriber.
- Keep Linux on Unix sockets; hide transport behind a small interface so clients don’t care.

## 4) Packaging notes

- Update `pyproject.toml` classifiers to include Windows once CI passes.
- Keep Windows-only functionality behind optional extras if we end up needing any extra dependency.
