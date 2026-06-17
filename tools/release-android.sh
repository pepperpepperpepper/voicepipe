#!/usr/bin/env bash
set -euo pipefail

# Build the Zwangli Android release APK and publish it to the self-hosted
# F-Droid repo at fdroid.uh-oh.wtf. After this completes, F-Droid clients
# pick up the new versionCode on their next refresh.
#
# Models ~/wtf-notifier/tools/release-android.sh. The APK is built UNSIGNED;
# publish-live.sh signs it with the central F-Droid keystore (the shared
# CN=fdroid.uh-oh.wtf key), so no per-app keystore is needed here.
#
# Reads versionCode/versionName from android/app/build.gradle.kts — no flags.
# Bump the version there, commit, then run this (or just `git push` with the
# tools/git-hooks pre-push hook active).
#
# Required env (loaded from ~/.api-keys by publish-live.sh):
#   FDROID_AWS_BUCKET / FDROID_AWS_ACCESS_KEY_ID / FDROID_AWS_SECRET_KEY
#   FDROID_AWS_CF_DISTRIBUTION_ID / FDROID_KEYSTORE_PASS / FDROID_KEY_PASS
#
# Optional env:
#   VOICEPIPE_FDROID_DIR   F-Droid repo dir (default: ~/fdroid -> /mnt/subtitled/fdroid)
#   VOICEPIPE_RELEASE_DRY  if set, pass --dry-run to publish-live.sh

APP_ID="dev.voicepipe.zwangli"

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
android_dir="$repo_root/android"
fdroid_dir="${VOICEPIPE_FDROID_DIR:-$HOME/fdroid}"
publish_script="$fdroid_dir/publish-live.sh"

[[ -d "$android_dir" ]] || { echo "error: android dir not found at $android_dir" >&2; exit 1; }
[[ -x "$publish_script" ]] || { echo "error: publish script not found/executable: $publish_script" >&2; exit 1; }

# Warn (don't block) if the tree/upstream is out of sync — the APK reflects
# the working tree, not origin.
if ! git -C "$repo_root" diff --quiet || ! git -C "$repo_root" diff --cached --quiet; then
  echo "warning: working tree has uncommitted changes; the published APK reflects local state" >&2
fi
upstream_ref="$(git -C "$repo_root" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
if [[ -n "$upstream_ref" ]] && ! git -C "$repo_root" merge-base --is-ancestor "@{u}" HEAD; then
  echo "warning: HEAD not pushed to $upstream_ref; remote will be behind the published APK" >&2
fi

version_name="$(awk -F\" '/versionName = /{print $2; exit}' "$android_dir/app/build.gradle.kts")"
version_code="$(awk '/versionCode = /{print $3; exit}' "$android_dir/app/build.gradle.kts")"
[[ -n "$version_name" && -n "$version_code" ]] || { echo "error: failed to parse version from build.gradle.kts" >&2; exit 1; }

# Refuse to re-publish an existing versionCode (Android refuses downgrades and
# F-Droid keys index entries by versionCode) — catches the "forgot to bump".
published_apk="$fdroid_dir/repo/${APP_ID}_${version_code}.apk"
if [[ -f "$published_apk" ]]; then
  echo "error: ${APP_ID}_${version_code}.apk is already published in $fdroid_dir/repo/" >&2
  echo "       bump versionCode + versionName in $android_dir/app/build.gradle.kts" >&2
  echo "       (and add android/fastlane/metadata/android/en-US/changelogs/<code>.txt)" >&2
  exit 1
fi

# Clear any stale unsigned APK for this versionCode from an aborted run.
rm -f "$fdroid_dir/unsigned/${APP_ID}_${version_code}.apk"

echo "==> Building Zwangli release $version_name (versionCode $version_code)"
( cd "$android_dir" && ./gradlew :app:assembleRelease )

apk_path="$android_dir/app/build/outputs/apk/release/app-release-unsigned.apk"
[[ -f "$apk_path" ]] || { echo "error: expected unsigned APK at $apk_path" >&2; exit 1; }

publish_args=()
[[ -n "${VOICEPIPE_RELEASE_DRY:-}" ]] && publish_args+=(--dry-run)
publish_args+=("$apk_path" "$APP_ID")

echo "==> Publishing to $fdroid_dir"
"$publish_script" "${publish_args[@]}"

echo "==> Done. https://fdroid.uh-oh.wtf/repo/${APP_ID}_${version_code}.apk"
