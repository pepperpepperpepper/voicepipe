#!/usr/bin/env bash
# Build an unsigned release APK and print next-step instructions for
# publishing via the central F-Droid repo. Does NOT sign or publish
# anything — F-Droid handles signing centrally at publish time.
#
# Usage:
#   android/scripts/release.sh            # full build, prints publish instructions
#   android/scripts/release.sh --dry-run  # print what would happen; no build
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  release.sh [--dry-run]

Assembles app/build/outputs/apk/release/app-release-unsigned.apk via
the project's Gradle wrapper. Prints the exact command to hand the
APK to /mnt/subtitled/fdroid/publish-live.sh — but does not run it.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

DRY_RUN=0
case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
  --dry-run)
    DRY_RUN=1
    ;;
  '')
    ;;
  *)
    die "unknown argument: $1 (try --help)"
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$ANDROID_DIR/.." && pwd)"
APK_PATH="$ANDROID_DIR/app/build/outputs/apk/release/app-release-unsigned.apk"
FDROID_PUBLISH="/mnt/subtitled/fdroid/publish-live.sh"
APP_ID="dev.voicepipe.zwangli"

if (( DRY_RUN )); then
  printf 'Dry run.\n'
  printf '  Would cd %s\n' "$ANDROID_DIR"
  printf '  Would run ./gradlew --no-daemon :app:assembleRelease\n'
  printf '  Would produce %s\n' "$APK_PATH"
  printf '  Then: %s %s %s\n' "$FDROID_PUBLISH" "$APK_PATH" "$APP_ID"
  exit 0
fi

cd "$ANDROID_DIR"

# Honour ANDROID_HOME / JAVA_HOME if the caller has them; otherwise
# fall back to the conventional local paths used elsewhere in this repo.
export ANDROID_HOME="${ANDROID_HOME:-$HOME/android-sdk}"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-21-openjdk}"

printf 'Building unsigned release APK...\n'
./gradlew --no-daemon :app:assembleRelease

[[ -f "$APK_PATH" ]] || die "release APK not found at $APK_PATH after assembleRelease"

apk_bytes="$(stat -c %s "$APK_PATH")"
printf '\nBuilt: %s (%s bytes)\n' "$APK_PATH" "$apk_bytes"

cat <<EOF

Next steps (manual — do NOT automate from here):

  1. First release only: copy the metadata template into the F-Droid repo
     and edit if needed:
       cp $REPO_DIR/android/fdroid-metadata-template.yml \\
          /mnt/subtitled/fdroid/metadata/$APP_ID.yml

  2. Verify the publish will work (dry run, no S3/CloudFront writes):
       $FDROID_PUBLISH --dry-run "$APK_PATH" $APP_ID

  3. Publish to fdroid.uh-oh.wtf (signs with the central keystore,
     uploads to S3, invalidates CloudFront — requires the
     FDROID_* env vars from ~/.api-keys):
       $FDROID_PUBLISH "$APK_PATH" $APP_ID

The publish script bumps CurrentVersion / CurrentVersionCode in the
metadata file from the APK's manifest automatically; no need to
hand-edit those fields on subsequent releases.
EOF
