#!/usr/bin/env bash
set -euo pipefail

# Start (or reuse) a Genymotion SaaS instance named "zwangli-android14" and
# connect adb to it.
#
# Output (stdout) is shell-safe assignments so callers can eval/source:
#   eval "$(android/scripts/gmsaas_start_and_connect.sh)"
# Then read:
#   $GMSAAS_INSTANCE_UUID
#   $GMSAAS_DEVICE_SERIAL
#
# Notes drawn from the gmsaas verification log in ZWANGLI_PLAN.md:
#  - Look up running instances by *name*, never by UUID — both the UUID and
#    the adb port change on every restart.
#  - `gmsaas instances start` blocks until ONLINE in current versions, so
#    the post-start poll loop is defensive belt-and-braces.
#  - Always pass `--max-run-duration` for ephemeral runs so an orphan
#    instance (from a crashed smoke run) does not bill indefinitely.
#
# Credentials are loaded from environment variables or from ~/.api-keys.
# This script never prints secrets.

RECIPE_UUID="${RECIPE_UUID:-9074ccc1-7aba-4c9b-b615-e69ef389738c}"  # Android 14.0 Phone
INSTANCE_NAME="${INSTANCE_NAME:-zwangli-android14}"
INSTANCE_UUID="${INSTANCE_UUID:-}"
MAX_RUN_DURATION_MINUTES="${MAX_RUN_DURATION_MINUTES:-${GMSAAS_MAX_RUN_DURATION_MINUTES:-5}}"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb is not in PATH." >&2
  exit 2
fi

GMSAAS_BIN="${GMSAAS_BIN:-gmsaas}"
if ! command -v "$GMSAAS_BIN" >/dev/null 2>&1; then
  for candidate in \
    "$HOME/.local/bin/gmsaas" \
    "/mnt/extra/pipx/bin/gmsaas" \
    "$HOME/.venvs/gmsaas/bin/gmsaas"; do
    if [[ -x "$candidate" ]]; then
      GMSAAS_BIN="$candidate"
      break
    fi
  done
fi
if ! command -v "$GMSAAS_BIN" >/dev/null 2>&1; then
  echo "gmsaas not in PATH. Install with: pipx install gmsaas" >&2
  exit 2
fi

API_KEYS_FILE="${API_KEYS_FILE:-$HOME/.api-keys}"
if [[ -z "${GENYMOTION_API_TOKEN:-}" && -z "${GENYMOTION_API_KEY:-}" && -f "$API_KEYS_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$API_KEYS_FILE"
fi

if [[ -z "${GENYMOTION_API_TOKEN:-}" && -n "${GENYMOTION_API_KEY:-}" ]]; then
  export GENYMOTION_API_TOKEN="$GENYMOTION_API_KEY"
fi

resolve_uuid_by_name() {
  "$GMSAAS_BIN" instances list \
    | awk -v name="$INSTANCE_NAME" 'NR>2 && $2==name && ($4=="ONLINE" || $4=="RUNNING") {print $1; exit}'
}

UUID=""
if [[ -n "$INSTANCE_UUID" ]]; then
  UUID="$INSTANCE_UUID"
else
  UUID="$(resolve_uuid_by_name || true)"
  if [[ -z "$UUID" ]]; then
    echo "No running '$INSTANCE_NAME' instance; starting one (recipe $RECIPE_UUID, max ${MAX_RUN_DURATION_MINUTES}m)..." >&2

    START_ARGS=(--max-run-duration "$MAX_RUN_DURATION_MINUTES")

    set +e
    START_OUT=$("$GMSAAS_BIN" instances start "${START_ARGS[@]}" "$RECIPE_UUID" "$INSTANCE_NAME" 2>&1)
    RET=$?
    set -e
    echo "$START_OUT" >&2
    if [[ $RET -ne 0 || "$START_OUT" == *"LICENSE_EXPIRED"* || "$START_OUT" == *"TOO_MANY_RUNNING_VDS"* ]]; then
      echo "Unable to start instance (rc=$RET). If TOO_MANY_RUNNING_VDS, stop another instance first (account quota = 1 concurrent VD)." >&2
      exit 3
    fi

    for _ in {1..90}; do
      UUID="$(resolve_uuid_by_name || true)"
      [[ -n "$UUID" ]] && break
      sleep 2
    done
  fi
fi
if [[ -z "$UUID" ]]; then
  echo "Failed to locate a running instance UUID." >&2
  exit 4
fi
echo "Instance UUID: $UUID" >&2

echo "Connecting adb to $UUID ..." >&2
ADB_OUT=$("$GMSAAS_BIN" instances adbconnect "$UUID")
echo "$ADB_OUT" >&2
SERIAL=$(echo "$ADB_OUT" | tr -d '\r' | grep -Eo '([A-Za-z0-9_.-]+:[0-9]+)' | tail -n 1 || true)
if [[ -z "$SERIAL" ]]; then
  echo "Failed to extract adb serial from: $ADB_OUT" >&2
  exit 5
fi

echo "Waiting for device $SERIAL ..." >&2
adb -s "$SERIAL" wait-for-device

printf 'GMSAAS_INSTANCE_UUID=%q\n' "$UUID"
printf 'GMSAAS_DEVICE_SERIAL=%q\n' "$SERIAL"
