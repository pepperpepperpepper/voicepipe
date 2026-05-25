#!/usr/bin/env bash
set -euo pipefail

# Phase 3a smoke test.
#
# 1. Start (or reuse) a Genymotion SaaS instance via gmsaas_start_and_connect.sh
# 2. Build app-debug.apk (skipped if it already exists and --no-build given)
# 3. Install the APK on the instance
# 4. Launch MainActivity
# 5. Verify it's the foreground activity via dumpsys
# 6. Take a screenshot to /tmp/zwangli-smoke.png
#
# Run from the repo root or from android/. Usage:
#   android/scripts/smoke.sh           # build + install + screenshot
#   android/scripts/smoke.sh --no-build
#
# Required env (typically set in ~/.api-keys, read by gmsaas script):
#   GENYMOTION_API_TOKEN (or GENYMOTION_API_KEY)
#
# Optional env:
#   SCREENSHOT_PATH   default /tmp/zwangli-smoke.png
#   APK_PATH          default app/build/outputs/apk/debug/app-debug.apk

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$(dirname "$HERE")"
cd "$ANDROID_DIR"

NO_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-build) NO_BUILD=1 ;;
    *) echo "unknown arg: $arg" >&2; exit 64 ;;
  esac
done

APK_PATH="${APK_PATH:-app/build/outputs/apk/debug/app-debug.apk}"
SCREENSHOT_PATH="${SCREENSHOT_PATH:-/tmp/zwangli-smoke.png}"
PACKAGE="dev.voicepipe.zwangli"
ACTIVITY=".MainActivity"

if [[ $NO_BUILD -eq 0 ]]; then
  echo "==> Building debug APK"
  ./gradlew --no-daemon :app:assembleDebug
fi
if [[ ! -f "$APK_PATH" ]]; then
  echo "APK not found at $APK_PATH" >&2
  exit 1
fi

echo "==> Bringing up Genymotion instance"
eval "$("$HERE/gmsaas_start_and_connect.sh")"
echo "instance=$GMSAAS_INSTANCE_UUID serial=$GMSAAS_DEVICE_SERIAL"

cleanup_adb() {
  if [[ -n "${GMSAAS_DEVICE_SERIAL:-}" ]]; then
    adb disconnect "$GMSAAS_DEVICE_SERIAL" >/dev/null 2>&1 || true
  fi
}
trap cleanup_adb EXIT

ADB="adb -s $GMSAAS_DEVICE_SERIAL"

DISPATCH_HOST_PORT="${DISPATCH_HOST_PORT:-8765}"

echo "==> Installing $APK_PATH"
$ADB install -r "$APK_PATH"

echo "==> Reverse-forwarding tcp:$DISPATCH_HOST_PORT (device -> host) so the app can reach the dispatch server"
# NOTE: Genymotion SaaS (cloud VMs) does NOT proxy `adb reverse` end-to-end
# in practice — the host setup completes but app-level TCP connections from
# the device to localhost:<port> still fail. Verified 2026-05-25. The local
# command below still succeeds, so we keep it for the local-Genymotion /
# real-device-on-USB case.
$ADB reverse "tcp:$DISPATCH_HOST_PORT" "tcp:$DISPATCH_HOST_PORT"

if ! curl -fsS "http://127.0.0.1:$DISPATCH_HOST_PORT/health" >/dev/null 2>&1; then
  echo "WARNING: no dispatch server on localhost:$DISPATCH_HOST_PORT — start one with 'voicepipe serve' before the in-app Send button will work." >&2
fi

echo "==> Launching $PACKAGE/$ACTIVITY"
$ADB shell am start -n "$PACKAGE/$ACTIVITY"

sleep 3

echo "==> Confirming foreground activity"
if ! $ADB shell dumpsys activity activities | grep -q "$PACKAGE/$ACTIVITY"; then
  echo "MainActivity not found in dumpsys output" >&2
  $ADB shell dumpsys activity activities | tail -50 >&2
  exit 6
fi

echo "==> Capturing screenshot to $SCREENSHOT_PATH"
$ADB exec-out screencap -p > "$SCREENSHOT_PATH"
echo "Screenshot: $SCREENSHOT_PATH ($(stat -c '%s' "$SCREENSHOT_PATH") bytes)"

echo "SMOKE OK"
