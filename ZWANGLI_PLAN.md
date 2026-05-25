# Zwangli — Android client build plan

Companion to the [architecture explainer](https://tmp.uh-oh.wtf/2026/05/25/591190b7-zwingli-architecture.html). This document is the operational roadmap for the Android side of the Zwingli/Zwangli split: each phase is one PR, with explicit scope, verification, and "done" criteria. Lives in the repo so it can evolve as we execute.

---

## Current state (2026-05-25)

| Phase | Status | PR |
|---|---|---|
| 1 — Actuator protocol + DesktopActuator | **MERGED** | #7 (`871704f`) |
| 2 — FastAPI dispatch server | **MERGED** | #8 (`8e0a949`) |
| 3a — Android skeleton + dispatch client + Genymotion smoke | next |
| 3b — AccessibilityService actuator | blocked on 3a |
| 3c — STT integration | blocked on 3b |
| 3d — Persistent notification + foreground service | blocked on 3c |
| 3e — `client_actions[]` execution (clipboard, feedback) | blocked on 3d |
| 3f (optional) — Custom wake word ("zwangli") | deferred |
| 4 (optional) — On-device offline dispatch | **deferred indefinitely** |

---

## North star

1. User speaks `"zwangli rewrite the email to mom"` into their phone.
2. Phone records audio → on-device STT → transcript.
3. Phone POSTs `{transcript, capabilities}` to the dispatch server over HTTPS.
4. Server runs the dispatcher with a `ServerActuator(capabilities=...)`.
5. Server returns `{output_text, payload, client_actions[]}`.
6. Phone types `output_text` into the focused field via `AccessibilityService`.
7. Phone executes `client_actions[]` (clipboard copies, audio feedback events).
8. Same `triggers.json` drives desktop Zwingli and Android Zwangli — one config, two arms.

---

## Working principles

1. **One phase = one PR**, ≤ 1 day of work, behind CI build verification.
2. **Genymotion smoke test is required to merge** any phase that adds user-facing UI/behaviour. CI build proves it compiles; Genymotion proves it actually runs.
3. **F-Droid release only after Phase 3e** (full feature set). Intermediate APKs are debug-only.
4. **No code duplication of the dispatcher.** The Android app is a Kotlin shell that talks HTTP to the existing Python dispatcher. We do not port the dispatcher to Kotlin unless Phase 4 happens.
5. **Skip Phase 4 (offline on-device) unless real demand emerges.** Always-connected is simpler and ~all use cases work with it. If we ever need offline, port to Kotlin (not Chaquopy/Python) — the ~10–15MB APK bloat and ~500ms cold-start aren't worth reusing the Python dispatcher 1:1 on-device.
6. **Capabilities-as-contract.** Every release of the Android app advertises a `capabilities[]` set to the server. New verbs degrade gracefully on older clients.

---

## Tech choices

- **Language**: Kotlin (no Java fallback)
- **Build system**: Gradle with Kotlin DSL (`build.gradle.kts`) + version catalog (`libs.versions.toml`)
- **Min SDK**: API 24 (Android 7.0, ~99% coverage in 2026) — confirmed
- **Target SDK**: API 34 (Android 14)
- **Package name**: `dev.voicepipe.zwangli` — confirmed (we claim the `dev.voicepipe` namespace for F-Droid)
- **HTTP**: OkHttp 4.x
- **JSON**: kotlinx.serialization
- **UI**: Plain `View` system to start (no Compose) — minimum surface for 3a, swap later if needed
- **Tests**: JUnit 4 + OkHttp `MockWebServer` for unit tests; instrumented tests deferred

---

## Phase 3a — Skeleton + dispatch client + Genymotion smoke

**Scope.** A barely-functional app that proves the HTTP wire end-to-end. No AccessibilityService, no STT, no notification. Just enough to type a transcript into a text box, POST it to a dispatch server, and see the response.

**Files added:**

```
android/
├── build.gradle.kts                       # root build
├── settings.gradle.kts                    # module declarations
├── gradle/
│   ├── libs.versions.toml                 # version catalog
│   └── wrapper/
│       ├── gradle-wrapper.jar             # committed wrapper
│       └── gradle-wrapper.properties
├── gradlew, gradlew.bat                   # wrapper scripts
└── app/
    ├── build.gradle.kts                   # app module build
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml        # INTERNET, MainActivity
        │   ├── kotlin/dev/voicepipe/zwangli/
        │   │   ├── MainActivity.kt        # text inputs + Send button + response TextView
        │   │   ├── DispatchClient.kt      # POST /dispatch, OkHttp + kotlinx.serialization
        │   │   └── Dto.kt                 # DispatchRequest, DispatchResponse, ClientAction
        │   └── res/
        │       ├── layout/activity_main.xml
        │       ├── values/strings.xml
        │       └── values/themes.xml
        └── test/kotlin/dev/voicepipe/zwangli/
            └── DispatchClientTest.kt      # MockWebServer round-trips
```

**Behaviour:**

- App launches → MainActivity shows three EditTexts (server URL, bearer token, transcript) + Send button + response TextView.
- Defaults: server URL = `http://10.0.2.2:8765` (Genymotion's host-loopback alias), token = empty, transcript = `"zwingli strip hello"`.
- Tap Send → POST `/dispatch` with JSON body, parse response, display in TextView.
- Network errors render as `"⚠ HTTP error: <reason>"` (Android UI thread).
- No persistence — settings reset on app close. (Settings persistence is Phase 3b material.)

**Tests:**

- `DispatchClient_sends_correct_json_body`
- `DispatchClient_attaches_bearer_when_token_set`
- `DispatchClient_omits_bearer_when_token_blank`
- `DispatchClient_parses_typical_response`
- `DispatchClient_parses_empty_client_actions`
- `DispatchClient_surfaces_non_2xx_as_exception`

All via `MockWebServer`; no network or device required.

**CI:**

- New job `android-build` in `.github/workflows/android.yml`:
  - Ubuntu runner, JDK 17
  - Cache `~/.gradle`
  - `./gradlew :app:assembleDebug :app:testDebugUnitTest`
  - Upload `app/build/outputs/apk/debug/app-debug.apk` as a workflow artifact

**Genymotion smoke (must pass before merge):**

- Local: `adb -s <genymotion-host:port> install -r app-debug.apk`
- Launch MainActivity, fill in server URL pointing at a running `voicepipe serve` on the host
- Type `"zwingli strip hello"` → tap Send → response TextView shows `output_text: "hello"`
- Screenshot attached to PR description

**Done means:**

1. `./gradlew assembleDebug` succeeds locally
2. JUnit unit tests green locally and in CI
3. CI `android-build` job green
4. Genymotion smoke screenshot in PR description
5. README has a new `### Android client (Zwangli)` subsection pointing at `android/README.md`

**Out of scope for 3a (called out so we don't scope-creep):**

- No `AccessibilityService` (Phase 3b)
- No `SpeechRecognizer` (Phase 3c)
- No persistent notification (Phase 3d)
- No `client_actions[]` execution (Phase 3e)
- No settings persistence (Phase 3b)
- No HTTPS / certificate pinning (Phase 3d when we start talking to the server over the open internet)
- No FDroid release (Phase 3e+)

---

## Phase 3b — AccessibilityService actuator

**Scope.** Make the app actually type the response into whatever app the user is in.

- New `ZwangliAccessibilityService` declared in manifest with `accessibilityservice/config.xml`.
- Capability: find the currently-focused `AccessibilityNodeInfo` with `ACTION_FOCUS` and `setText(...)` on it.
- MainActivity loses its "response goes here" TextView in favour of "response was typed into focused field." (Keep a small history pane for debugging.)
- New `SettingsActivity` for: server URL, bearer token (persisted via `SharedPreferences`), "Enable Zwangli accessibility service" button that opens Android's accessibility settings.
- First-launch banner: "Grant accessibility permission to enable typing."

**Tests:**

- `ZwangliAccessibilityService_types_into_focused_node` — instrumented test, deferred to Phase 4-ish unless cheap.
- Unit tests for the settings persistence + URL validation.

**Genymotion smoke:** Open Genymotion's built-in Messaging app, focus a compose field, switch to Zwangli, send `"zwingli strip hello world"`, switch back to Messaging — `"hello world"` appears in the compose field.

**Done means:** typing a transcript in Zwangli causes text to appear in whatever app was last focused.

---

## Phase 3c — STT integration

**Scope.** Replace the transcript EditText with a microphone button that uses Android's `SpeechRecognizer`.

- `RECORD_AUDIO` permission added to manifest + runtime permission prompt
- Mic button starts a `SpeechRecognizer` session (`RecognizerIntent.EXTRA_LANGUAGE_MODEL_FREE_FORM`)
- Show partial results while speaking; commit final transcript on `onResults`
- Send committed transcript to dispatch as before
- Fallback: if no on-device recognizer is available (e.g. some Genymotion images), show error and keep text input as escape hatch

**Tests:** unit tests for the recognizer-result handler (no actual mic needed).

**Genymotion smoke:** push a known audio file via `adb push`, use `adb shell input keyevent` to start recording, verify the result is dispatched. (Genymotion supports virtual mic via `adb shell setprop` in some images.) If this proves fragile, fall back to manual smoke on a real device.

---

## Phase 3d — Persistent notification + foreground service

**Scope.** The "explicit launch" coexistence pattern from the architecture doc.

- New `ZwangliForegroundService` that posts a persistent notification with a "Dictate" action button
- Tapping "Dictate" launches the recording flow directly (no need to open the main activity)
- Service stays alive across reboots via `BOOT_COMPLETED` receiver (opt-in toggle in settings)
- Notification channel created on first run; user can disable from system settings if they want

**Tests:** unit tests for notification builder; instrumented test for service lifecycle deferred.

**Genymotion smoke:** notification appears in status bar; tapping "Dictate" starts a recording session.

---

## Phase 3e — `client_actions[]` execution + FDroid release

**Scope.** Honor the server's queued side-effects, then ship.

- Iterate `client_actions[]` from each `/dispatch` response:
  - `{type: "clipboard", text}` → `ClipboardManager.setPrimaryClip(...)`
  - `{type: "feedback", event}` → play a short sound (`MediaPlayer` from raw resources) keyed by event name
- Pre-bundled sounds: `success.ogg`, `error.ogg`, `match.ogg` (CC0-licensed from freesound.org or similar)
- App capability advertisement: now reports `["clipboard", "audio_feedback"]` in every `/dispatch` request (still no `subprocess` — shell stays server-side)

**FDroid release:**

- Generate signing keystore on first release; commit public cert (`zwangli-release.cert`) for verification.
- Add `signingConfigs { release { ... } }` block to `app/build.gradle.kts`, read key password from env (CI secret or local `~/.gradle/gradle.properties`).
- New script `android/scripts/release.sh` that:
  1. `./gradlew :app:assembleRelease`
  2. Copies the signed APK to the FDroid repo dir
  3. Runs `fdroid update -c` (signature index) and `fdroid server update` (publish)
- New metadata file in the FDroid repo: `metadata/dev.voicepipe.zwangli.yml`

**Done means:** app published to FDroid, installable on user's phone via F-Droid client, voice → text in focused app works end-to-end.

---

## Cross-cutting infrastructure

### Genymotion SaaS

- **Pattern**: port `~/AnySoftKeyboard/scripts/gmsaas_start_and_connect.sh` to `android/scripts/gmsaas_start_and_connect.sh`. The script auto-resolves an existing instance by name (`zwangli-android14`) or starts a fresh one from recipe UUID `9074ccc1-7aba-4c9b-b615-e69ef389738c` (Android 14.0 Phone), then `gmsaas instances adbconnect <uuid>` returns the adb serial in `host:port` form. Output is shell-safe assignments for `eval`.
- **Prereq**: install via `pipx install gmsaas` (Arch's Python enforces PEP 668 so plain `pip install --user` fails — binary lands at `/mnt/extra/pipx/bin/gmsaas`). Credentials come from `GENYMOTION_API_KEY` in `~/.api-keys`.
- **Local dev loop:**
  ```bash
  eval "$(android/scripts/gmsaas_start_and_connect.sh)"
  cd android
  ./gradlew :app:installDebug   # uses ANDROID_SERIAL=$GMSAAS_DEVICE_SERIAL
  adb -s $GMSAAS_DEVICE_SERIAL shell am start -n dev.voicepipe.zwangli/.MainActivity
  adb -s $GMSAAS_DEVICE_SERIAL exec-out screencap -p > /tmp/zwangli-smoke.png
  ```
- **For PRs:** every phase that adds user-facing surface runs `android/scripts/smoke.sh` which wraps the above + an "app launched successfully" check via `adb shell dumpsys activity activities`. Screenshot is attached to the PR description.

### F-Droid repo

- **Target**: existing self-hosted repo at `/mnt/subtitled/fdroid` (= `~/fdroid` symlink), public URL `https://fdroid.uh-oh.wtf/repo` (S3 + CloudFront).
- **Publish path**: `/mnt/subtitled/fdroid/publish-live.sh <unsigned-apk>`. Stages the unsigned APK in `unsigned/`, rewrites `metadata/<app_id>.yml` with the new `versionName` / `versionCode`, runs `fdroid publish` (signs with the central keystore), `fdroid update` (regenerates index), syncs `repo/`/`archive/`/`metadata/` to S3, and invalidates CloudFront.
- **Required env** (read from `~/.api-keys`): `FDROID_AWS_BUCKET`, `FDROID_AWS_ACCESS_KEY_ID`, `FDROID_AWS_SECRET_KEY`, `FDROID_AWS_CF_DISTRIBUTION_ID`, `FDROID_KEYSTORE_PASS`, `FDROID_KEY_PASS`. All already present for the other apps in the repo.
- **Metadata template**: copy `/mnt/subtitled/fdroid/metadata/com.wtfnotifier.yml` to `dev.voicepipe.zwangli.yml` and adjust `Name`, `Summary`, `Description`, `SourceCode`, `WebSite`. Borrow `~/AnySoftKeyboard/scripts/fdroid/generate_metadata.py` if useful for automation.
- **Signing**: handled centrally by `fdroid publish` using the repo's existing `keystore.jks`. Gradle produces an unsigned release APK (`app-release-unsigned.apk`); we do **not** generate a per-app keystore.

### Amazon Device Farm

- Deferred until Phase 3e+. Useful for instrumented tests across real device matrix once we have a non-trivial UI surface.
- No infra changes needed in earlier phases; just remember it's an option.

---

## Operational verification log

### 2026-05-25 — gmsaas + zwangli-android14 cold start

Verified the entire Genymotion path end-to-end before writing any Android code, so Phase 3a's smoke step is known-good.

**Install** — `pipx install gmsaas` → `gmsaas 1.16.0` at `/mnt/extra/pipx/bin/gmsaas`. Plain `pip install --user` fails on Arch (PEP 668 "externally managed environment"). An earlier broken venv (built against Python 3.13 before the system upgraded to 3.14) was fixed with `pipx reinstall gmsaas`.

**Auth** — `gmsaas doctor` → `Authentication OK. Android SDK OK.` No login dance needed; existing creds in `~/.api-keys` (`GENYMOTION_API_KEY`) were valid. The Android SDK at `~/android-sdk` was already configured in `~/.Genymobile/gmsaas/config.json`.

**Quota** — account allows **1 concurrent running VD**. First attempt to start `zwangli-android14` while `nsk-android14` was running returned `403 TOO_MANY_RUNNING_VDS`. The Phase 3a smoke script must therefore either reuse a running instance of the requested name or stop-and-start; it cannot assume a free slot.

**Cold-start sequence** (5-step swap, see chat log):

```
1. gmsaas instances stop <nsk-uuid>                                         # free the slot
2. gmsaas instances start --max-run-duration 5 9074ccc1-... zwangli-android14
   → 2a0e5753-6f4e-43f6-89e7-2b6a2e7dc43e                                   # new UUID, blocks until ONLINE
3. gmsaas instances adbconnect 2a0e5753-...                                 # → localhost:34919
   adb -s localhost:34919 wait-for-device
   getprop ro.product.model           → zwangli-android14                   # device labels match name
   getprop ro.build.version.release   → 14
   getprop sys.boot_completed         → 1
4. gmsaas instances stop 2a0e5753-...                                       # release the slot
5. gmsaas instances start 9074ccc1-... nsk-android14                        # restore prior state
   → 361ee71d-ef54-4734-a21f-9f44d351cf34                                   # NEW uuid (see "Important" below)
   gmsaas instances adbconnect 361ee71d-...                                 # → localhost:41247
```

**Important script-writing rules drawn from this:**

- **Look up by name, never by UUID.** Both the instance UUID and the adb port change on every restart. `nsk-android14` went from `4f4e21b3-…@localhost:38325` to `361ee71d-…@localhost:41247` across a single stop/start. The `gmsaas_start_and_connect.sh` pattern in `~/AnySoftKeyboard/scripts/` does this correctly — port it verbatim, just change the default name to `zwangli-android14`.
- **`gmsaas instances start` blocks until ONLINE.** No need for the 90-iteration poll loop the AnySoftKeyboard script uses — that's defensive code from an older gmsaas version. A single foreground call is sufficient. The poll loop is still fine to keep for safety on retries.
- **Always pass `--max-run-duration N` for ephemeral runs.** Without it, an instance left orphaned by a crashed smoke run keeps billing indefinitely. 5 minutes is enough for any 3a-3e smoke test; bump up for instrumented test suites later.
- **`adb disconnect <serial>` before `gmsaas instances stop`** to keep the local adb-server clean. Not strictly required but avoids stale entries in `adb devices`.

---

## Explicitly out of scope

| Item | Reason |
|---|---|
| Phase 4 (on-device dispatcher) | Always-connected works for ~all use cases; embedding Python via Chaquopy costs ~15MB APK + ~500ms cold-start. If offline becomes essential, port to Kotlin instead. |
| iOS client | Not planned. |
| Multi-user / session scoping on the server | Single-user assumption is fine until proven otherwise. |
| WebSocket push (server → phone) | Polling `/log/tail` is adequate; revisit only if "instant log mirror" becomes a feature. |
| Compose-based UI | View system is faster to ship for 3a–3e; Compose is fine to revisit later. |
| Bringing the old voicepipe Python dispatcher onto the phone in any form | See Phase 4 reasoning. |

---

## Open questions

All resolved as of plan v1. Decisions captured above:

1. ~~Genymotion connection~~ — `gmsaas` CLI (pipx-installed) + AnySoftKeyboard pattern, recipe `9074ccc1-7aba-4c9b-b615-e69ef389738c`, instance name `zwangli-android14`. Credentials in `~/.api-keys`. **Cold-start path verified end-to-end 2026-05-25** — see "Operational verification log" below.
2. ~~F-Droid distribution~~ — existing repo at `/mnt/subtitled/fdroid`, publish via `publish-live.sh`, central keystore.
3. ~~Package name~~ — `dev.voicepipe.zwangli`.
4. ~~Min SDK~~ — API 24.
5. ~~Signing key~~ — moot; central FDroid keystore signs at publish time.

---

## Glossary

- **Dispatcher** — the pure-Python verb resolution / chain splitting / alias / fuzzy-match logic in `voicepipe/transcript_triggers/`. Same code runs in the desktop daemon and the dispatch server.
- **Actuator** — the OS-touching boundary the dispatcher consults for `run_subprocess`, `set_clipboard`, `play_feedback`. `DesktopActuator` runs everything locally; `ServerActuator` runs subprocess locally and queues clipboard/feedback as `client_actions[]`; the future `AndroidActuator` won't exist (the Android app is a thin client; the server does the dispatching).
- **Capabilities** — the set of `{subprocess, clipboard, audio_feedback}` strings a client advertises. Drives the dispatcher's graceful-skip behaviour for verbs the client can't support.
- **Zwingli / Zwangli** — Q-word swap that lets desktop and phone disambiguate which device an utterance is addressed to.
