# Voicepipe Refactor TODOs — Systemd & Configuration

This TODO list focuses on the two pain points:
1) starting/stopping Voicepipe feels complex (two units + multiple commands), and
2) the API key/config behaves differently in systemd vs interactive shells (because systemd user services don’t load `.bashrc` / `.zshrc`).

Conventions:
- **P0**: fixes “can’t use it” / common breakages
- **P1**: reduces complexity / improves UX & debug-ability
- **P2**: maintainability / future-proofing

---

## P0 — One Source Of Truth For API Key + Config

- [ ] **Create a shared config module and use it everywhere**
  - Problem: config is currently loaded in different places differently (CLI loads `.env`, transcriber daemon relies on process env), so behavior changes depending on how you launched Voicepipe.
  - Tasks:
    - Add `voicepipe/config.py` (or `voicepipe/settings.py`) with helpers like:
      - `get_openai_api_key()` (never prints key; returns str or raises)
      - `get_model_default()` (unify `VOICEPIPE_TRANSCRIBE_MODEL` / `VOICEPIPE_MODEL` / CLI `--model`)
      - `load_optional_env()` (optional: `.env` and/or `~/.config/voicepipe/voicepipe.env`)
    - Update key consumers to use it:
      - `voicepipe/transcriber.py`
      - `voicepipe/transcriber_daemon.py`
      - `voicepipe/cli.py` (and any other entrypoint that instantiates a transcriber)
  - Acceptance:
    - `voicepipe` and `voicepipe-transcriber-daemon` resolve the API key/config the same way.
    - Errors clearly say where Voicepipe looked for config (without leaking secrets).

- [ ] **Standardize on a systemd-friendly env file**
  - Problem: systemd user services don’t inherit shell init env vars, so `export OPENAI_API_KEY=...` in `.bashrc` won’t exist inside `voicepipe-transcriber.service`.
  - Tasks:
    - Choose a canonical env file path (recommended):
      - `~/.config/voicepipe/voicepipe.env`
    - Update `voicepipe-transcriber.service.template` (and recorder unit if needed) to include:
      - `EnvironmentFile=-%h/.config/voicepipe/voicepipe.env`
    - Ensure this env file can also be used by non-systemd runs (via the shared config module).
  - Acceptance:
    - Setting the key in `~/.config/voicepipe/voicepipe.env` works for both:
      - `systemctl --user start voicepipe-transcriber.service`
      - `voicepipe stop` / `voicepipe-transcribe-file …`

---

## P1 — Make Start/Stop + Key Setup One-Command Simple

- [ ] **Add a first-class command to configure the OpenAI API key**
  - Goal: users should never need to edit service files or guess between `.bashrc` vs config files.
  - Tasks:
    - Add `voicepipe config set-openai-key`:
      - Writes/updates `~/.config/voicepipe/voicepipe.env` with `OPENAI_API_KEY=...`
      - Sets file mode to `0600`
      - Optional: `--from-stdin` to avoid key in shell history
    - Add `voicepipe config show`:
      - Prints which config sources exist and which values are set (booleans only; never print secrets)
  - Acceptance:
    - `voicepipe config set-openai-key --from-stdin` is sufficient for a fresh install.

- [ ] **Add “service management” subcommands**
  - Goal: hide systemd complexity behind one CLI.
  - Tasks:
    - Add `voicepipe service` subcommands:
      - `install` (writes user units from templates + daemon-reload)
      - `enable`, `disable`
      - `start`, `stop`, `restart`
      - `status`
      - `logs` (e.g. `journalctl --user-unit … -n 200 -f`)
    - Ensure commands are explicit about which units are affected:
      - `voicepipe-recorder.service`
      - `voicepipe-transcriber.service`
  - Acceptance:
    - A user can run: `voicepipe service install && voicepipe service enable && voicepipe service start`.
    - Stop/start doesn’t require remembering two unit names.

- [ ] **Refactor installer + docs to match the new config path**
  - Problem: the current flow encourages `.bashrc` exports, which systemd can’t see.
  - Tasks:
    - Update `install.sh` to:
      - Create `~/.config/voicepipe/voicepipe.env` (or print a guided prompt)
      - Stop recommending `.bashrc` for systemd users
      - Prefer `voicepipe config …` and `voicepipe service …` commands (once implemented)
    - Update `README.md` “API Key Setup” and “systemd services” sections to:
      - Explain why `.bashrc` exports don’t reach systemd services
      - Document the env file path as the recommended approach
  - Acceptance:
    - Fresh user path: install → set key once → start services works without extra Linux/systemd knowledge.

- [ ] **Add targeted diagnostics for systemd + key propagation**
  - Tasks:
    - Add `voicepipe doctor systemd`:
      - Checks unit presence, enabled state, running state
      - Checks `EnvironmentFile` exists and is readable
      - Suggests exact commands to fix (enable/start/restart)
    - Enhance `voicepipe doctor env` output to include:
      - Whether `~/.config/voicepipe/voicepipe.env` exists
      - Whether the OpenAI key is discoverable via the shared config loader
  - Acceptance:
    - When transcription fails due to missing key, doctor makes the root cause obvious in one run.

---

## P2 — Security / Polish / Future-Proofing

- [ ] **Support systemd credentials for secrets (optional)**
  - Idea: allow `LoadCredential=` / `SetCredential=` so secrets don’t live in env vars.
  - Acceptance: users who want “best practice” can opt in without breaking the simple env-file flow.

- [ ] **Deprecate old key locations (optional, with migration helper)**
  - Current supported key locations include `~/.config/voicepipe/api_key` and `~/.voicepipe_api_key`.
  - Task: add a `voicepipe config migrate` to populate `voicepipe.env` and warn about legacy locations.
