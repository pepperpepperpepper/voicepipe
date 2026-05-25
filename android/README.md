# Zwangli — Android client

The Android sibling of desktop Zwingli. A thin Kotlin client that POSTs a
transcript to a voicepipe dispatch server and renders the response.

Phase 3a (this folder, today) is the **skeleton + dispatch client only**:
no `AccessibilityService`, no STT, no notification, no `client_actions[]`
execution. It exists to prove the HTTP wire end-to-end so the rest of
the phases (3b–3e in [`../ZWANGLI_PLAN.md`](../ZWANGLI_PLAN.md)) can
build on it.

## Project layout

```
android/
├── build.gradle.kts           # root build
├── settings.gradle.kts        # module declarations
├── gradle.properties
├── gradle/
│   ├── libs.versions.toml     # version catalog
│   └── wrapper/               # committed Gradle wrapper
├── gradlew, gradlew.bat
├── app/
│   ├── build.gradle.kts
│   ├── proguard-rules.pro
│   └── src/
│       ├── main/
│       │   ├── AndroidManifest.xml
│       │   ├── kotlin/dev/voicepipe/zwangli/
│       │   │   ├── DispatchClient.kt   # POST /dispatch, OkHttp + kotlinx.serialization
│       │   │   ├── Dto.kt              # DispatchRequest, DispatchResponse
│       │   │   └── MainActivity.kt     # text input + Send button + response TextView
│       │   └── res/                    # layout, strings, theme
│       └── test/kotlin/dev/voicepipe/zwangli/
│           └── DispatchClientTest.kt   # MockWebServer round-trips (8 tests)
└── scripts/
    ├── gmsaas_start_and_connect.sh    # bring up zwangli-android14
    └── smoke.sh                       # install APK + adb reverse + screenshot
```

## Build

Requires JDK 17+ (build verified locally with JDK 21) and Android SDK
with `platforms/android-34` + `build-tools/34.0.0` installed.

```bash
cd android
# Point Gradle at your SDK if it's not auto-detected:
echo "sdk.dir=$HOME/android-sdk" > local.properties

./gradlew :app:assembleDebug         # → app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest     # → 8 unit tests, MockWebServer-based
```

CI runs the same two tasks plus uploads the APK as a workflow artifact —
see [`../.github/workflows/android.yml`](../.github/workflows/android.yml).

## Run it against a real dispatch server

The dispatch server is the existing voicepipe one — same code that powers
the desktop daemon, wrapped in FastAPI. Start it on the host:

```bash
voicepipe serve                    # binds 127.0.0.1:8765
# (or, without the installed CLI:)
python -c "from voicepipe.dispatch_server import run; run()"
```

For Genymotion SaaS, the cloud VM can't reach your host's loopback
directly. Use `adb reverse` to bridge the dispatch port through the
adb connection:

```bash
eval "$(android/scripts/gmsaas_start_and_connect.sh)"
adb -s "$GMSAAS_DEVICE_SERIAL" reverse tcp:8765 tcp:8765
adb -s "$GMSAAS_DEVICE_SERIAL" install -r android/app/build/outputs/apk/debug/app-debug.apk
adb -s "$GMSAAS_DEVICE_SERIAL" shell am start -n dev.voicepipe.zwangli/.MainActivity
```

Inside the app, the default Server URL `http://localhost:8765` will
hit the host's dispatch server via the reverse tunnel. Type
`zwingli strip hello` → tap **Send** → response TextView shows
`ok=true / output_text=hello`.

## Smoke test

`scripts/smoke.sh` automates the above: it brings up
`zwangli-android14` via `gmsaas`, installs the APK, sets up
`adb reverse`, launches `MainActivity`, asserts it's foreground via
`dumpsys`, and writes a screenshot to `/tmp/zwangli-smoke.png`. Run
from anywhere:

```bash
android/scripts/smoke.sh                       # build then smoke
android/scripts/smoke.sh --no-build            # reuse existing APK
SCREENSHOT_PATH=/tmp/x.png android/scripts/smoke.sh
```

Account quota note: Genymotion SaaS allows **1 concurrent running VD**
per account. If another instance is running (e.g.
`nsk-android14`), stop it before invoking the smoke or you'll get
`TOO_MANY_RUNNING_VDS`.

**Genymotion SaaS limitation.** `adb reverse` is set up locally but the
SaaS adb proxy does not forward reverse connections from the cloud VM
back through the tunnel — so the in-app Send button can't reach the
host's loopback dispatch server from a SaaS device. See the Phase 3a
verification note in [`../ZWANGLI_PLAN.md`](../ZWANGLI_PLAN.md) for
workarounds (local Genymotion Desktop, real device on USB, or a public
tunnel like `cloudflared`).

## What's next

`../ZWANGLI_PLAN.md` is the operational roadmap.

- **3b** ✅ — AccessibilityService: types the response into whatever
  app is focused
- **3c** ✅ — STT: mic button drives `SpeechRecognizer`; partial
  results stream into the transcript field, final result auto-submits.
  EditText stays as a fallback when no on-device recognizer is
  installed.
- **3d** ✅ — Foreground service + persistent notification. The
  notification's **Dictate** action launches MainActivity with
  `EXTRA_AUTO_LISTEN=true`, which triggers the mic flow on resume.
  A "Start on boot" checkbox in Settings re-arms the service after
  reboot via `BootReceiver`.
- **3e** ✅ — Executes `client_actions[]` (clipboard via
  `ClipboardManager`, audio feedback via `MediaPlayer` from
  `res/raw/*.ogg`). Capabilities `["clipboard", "audio_feedback"]`
  are advertised in every `/dispatch` request. Release signingConfig
  reads `ZWANGLI_KEYSTORE_*` from gradle properties or env; falls
  back to an unsigned release APK when unset. **F-Droid publish is
  not yet wired** — it touches the central keystore + S3 +
  CloudFront, so it stays a manual user-authorized step.

## Release flow

Three steps to publish a new Zwangli release to
[fdroid.uh-oh.wtf](https://fdroid.uh-oh.wtf/repo). The central F-Droid
keystore signs the APK at publish time — Zwangli does **not** ship its
own keystore.

1. **Build the unsigned release APK.**
   ```bash
   android/scripts/release.sh
   # → android/app/build/outputs/apk/release/app-release-unsigned.apk
   ```
   `--dry-run` prints what would run without invoking Gradle.

2. **First release only:** copy the metadata template into the F-Droid
   repo (and edit if needed — `Summary`, `Description`, etc.):
   ```bash
   cp android/fdroid-metadata-template.yml \
      /mnt/subtitled/fdroid/metadata/dev.voicepipe.zwangli.yml
   ```
   Later releases skip this step — `publish-live.sh` rewrites
   `CurrentVersion` / `CurrentVersionCode` from the APK manifest each
   time.

3. **Publish.** Verify with `--dry-run` first, then run for real:
   ```bash
   /mnt/subtitled/fdroid/publish-live.sh --dry-run \
       android/app/build/outputs/apk/release/app-release-unsigned.apk \
       dev.voicepipe.zwangli

   /mnt/subtitled/fdroid/publish-live.sh \
       android/app/build/outputs/apk/release/app-release-unsigned.apk \
       dev.voicepipe.zwangli
   ```
   Requires the `FDROID_AWS_*` and `FDROID_KEYSTORE_PASS` /
   `FDROID_KEY_PASS` env vars from `~/.api-keys`.

## Bundled audio

`res/raw/{success,error,match}.ogg` are short CC0-licensed feedback
tones (ascending chime / descending tone / single blip) generated
locally with ffmpeg's `sine` source + `afade` envelope. See
[`AUDIO_NOTICE.md`](AUDIO_NOTICE.md) for per-file durations, the
reproducible recipes, and license info.
