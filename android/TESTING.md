# Zwangli — on-device testing

The JVM unit tests under `app/src/test/` (run by
`./gradlew :app:testDebugUnitTest`) don't touch a device. This doc
covers the test paths that do.

## Instrumented tests (`connectedDebugAndroidTest`)

Verified on Genymotion `nsk-android14` SDK 34. Any device visible to
`adb devices` works — local emulator, Genymotion Desktop, Genymotion
SaaS via `gmsaas`, USB device.

```bash
adb devices                                  # at least one "device" line
./gradlew :app:connectedDebugAndroidTest
```

Coverage:

- **`ClientActionExecutorAndroidTest`** — instantiates a real
  `MediaPlayer` for each of `res/raw/{success,error,match}.ogg` and
  latches on `OnCompletionListener` to prove the .ogg files decode
  and play to completion on the device runtime (not just that the
  call returns).
- **`InjectTranscriptReceiverTest`** — drives the debug-only
  `INJECT_TRANSCRIPT` broadcast against an in-process `MockWebServer`
  and asserts the dispatch request body, capabilities advertisement,
  feedback count, and error-path reporting via the result broadcast.

## Ad-hoc `INJECT_TRANSCRIPT` smoke (manual)

The debug variant ships a `BroadcastReceiver` that runs an arbitrary
transcript through `DispatchPipeline` (HTTP → executor) without going
through the mic. Useful for one-off integration checks against a real
or mock dispatch server.

**Debug variant only.** The receiver is declared in
`src/debug/AndroidManifest.xml` and confirmed absent from the merged
release manifest — it is never on an F-Droid install.

### 1. Mock dispatch server (or use the real one)

Any HTTP endpoint that accepts `POST /dispatch` and returns a
`DispatchResponse` JSON works. Minimum reproducible recipe:

```python
# /tmp/mock_dispatch.py
import http.server, json, socketserver, time
PORT = 18765
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get('Content-Length','0'))
        body = self.rfile.read(n).decode('utf-8') if n else ''
        print(f"[{time.strftime('%H:%M:%S')}] POST {self.path} body={body}", flush=True)
        resp = json.dumps({
            "ok": True,
            "output_text": "hello from mock",
            "client_actions": [
                {"type":"feedback","event":"success"},
                {"type":"clipboard","text":"clip-from-mock"},
            ],
        }).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length', str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)
    def log_message(self,*a,**k): pass
with socketserver.TCPServer(('127.0.0.1', PORT), H) as s:
    print(f'listening on {PORT}', flush=True); s.serve_forever()
```

```bash
python3 /tmp/mock_dispatch.py > /tmp/mock_dispatch.log 2>&1 &
```

### 2. Install + bridge

```bash
./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb reverse tcp:18765 tcp:18765
```

`adb reverse` works on local Genymotion (`adb -s localhost:<port>`) and
USB devices. It does **not** forward on Genymotion SaaS — for SaaS
you'd hit a public dispatch URL directly or use a tunnel
(`cloudflared`/`ngrok`).

### 3. Unfreeze the package — the stopped-app gotcha

> **Android marks freshly-installed apps as "stopped" until first
> launch, and broadcasts to a stopped package are silently dropped.**
> `am broadcast` will still report `Broadcast completed: result=0`,
> but logcat will show no `InjectTranscriptReceiver` output and your
> dispatch server will see no request. This silent failure is the
> single most common confusion with this recipe.

Pick one workaround:

```bash
# A. Launch MainActivity once to unfreeze (persists until next force-stop)
adb shell am start -n dev.voicepipe.zwangli/.MainActivity

# B. Or set FLAG_INCLUDE_STOPPED_PACKAGES on the broadcast itself
#    (then you can skip step A every time)
adb shell am broadcast -f 32 -a dev.voicepipe.zwangli.INJECT_TRANSCRIPT ...
```

### 4. Fire the broadcast + read logcat

```bash
adb logcat -c
adb shell am broadcast \
    -a dev.voicepipe.zwangli.INJECT_TRANSCRIPT \
    --es transcript "zwingli e2e smoke" \
    --es server_url "http://localhost:18765" \
    -p dev.voicepipe.zwangli
sleep 2
adb logcat -d -s InjectTranscriptReceiver:V ClientActionExecutor:V
```

Success markers:

- **Logcat:**
  `InjectTranscriptReceiver: Injected transcript='zwingli e2e smoke' ok=true`
- **Mock server stdout:**
  `POST /dispatch body={"transcript":"zwingli e2e smoke","capabilities":["clipboard","audio_feedback"]}`

### 5. Cleanup

```bash
adb reverse --remove tcp:18765
pkill -f mock_dispatch.py
```

## Broadcast reference

**Action:** `dev.voicepipe.zwangli.INJECT_TRANSCRIPT`

| Extra | Type | Purpose |
|---|---|---|
| `transcript` | string (required) | What gets POSTed to `/dispatch` |
| `server_url` | string (optional) | Overrides saved Settings serverUrl for this call |
| `token` | string (optional) | Overrides saved bearer token for this call |

**Result action:** `dev.voicepipe.zwangli.INJECT_TRANSCRIPT_RESULT` —
sent back after the pipeline completes, with extras `ok` (bool),
`error` (string?), `output_text` (string?), `clipboard_applied` (int),
`feedback_played` (int), `unknown_skipped` (int).
`InjectTranscriptReceiverTest` consumes this; ad-hoc adb usage usually
just reads logcat instead.
