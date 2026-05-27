"""Helper for end-to-end Android tests that route a transcript through
the debug-only ``INJECT_TRANSCRIPT`` broadcast and observe the result
the receiver logs back.

Flow:
  1. ``adb shell am broadcast -f 32 -a INJECT_TRANSCRIPT --es transcript
     '<text>' --es server_url '<url>' -p dev.voicepipe.zwangli``
  2. The receiver runs the transcript through ``DispatchPipeline`` →
     ``ClientActionExecutor`` and (per the debug-variant patch in
     ``InjectTranscriptReceiver.kt``) prints a single ``INJECT_RESULT
     key=value …`` line to logcat.
  3. We tail ``logcat -d -s InjectTranscriptReceiver:I`` from
     ``logcat_since_marker`` and parse the first match.

The receiver's reply broadcast (``INJECT_TRANSCRIPT_RESULT``) carries
the same counters but can't be picked up by an external observer
without a second helper APK; the logcat line is the path of least
resistance and is fine for tests that already gate on adb access.

Skip-gates are provided as standalone helpers so call sites can compose
them (e.g. one test cares about accessibility, another doesn't).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass


DEFAULT_PACKAGE = "dev.voicepipe.zwangli"
INJECT_ACTION = "dev.voicepipe.zwangli.INJECT_TRANSCRIPT"
RECEIVER_TAG = "InjectTranscriptReceiver"

# ``-f 32`` = FLAG_INCLUDE_STOPPED_PACKAGES — Android quietly drops
# broadcasts to packages it considers "stopped" (freshly installed and
# never opened, or force-stopped). The flag bypasses that. Without it
# this helper silently no-ops on the first test of a session.
_BROADCAST_FLAG = "32"


@dataclass(frozen=True)
class InjectOutcome:
    ok: bool
    clipboard: int
    feedback: int
    intents: int
    global_actions: int
    unknown: int
    output_len: int
    error: str | None
    raw_log_line: str


def _adb(serial: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["adb", "-s", serial, *args]
    return subprocess.run(
        cmd, check=check, capture_output=True, text=True, timeout=30
    )


def device_is_available(serial: str) -> bool:
    """``adb devices`` lists the serial AND it's in the 'device' state
    (not 'offline' / 'unauthorized' / 'recovery')."""
    try:
        proc = subprocess.run(
            ["adb", "devices"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return False
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == serial and parts[1] == "device":
            return True
    return False


def app_is_installed(serial: str, package: str = DEFAULT_PACKAGE) -> bool:
    try:
        proc = _adb(serial, "shell", "pm", "list", "packages", package)
    except subprocess.SubprocessError:
        return False
    needle = f"package:{package}"
    return any(line.strip() == needle for line in proc.stdout.splitlines())


def dispatch_reachable_from_device(
    serial: str,
    *,
    server_url: str,
) -> bool:
    """``curl /health`` from the device. Confirms ``adb reverse`` (or the
    public URL) is wired up — the broadcast itself looks identical
    whether the dispatch call succeeds or fails, so we'd rather skip
    than mis-report."""
    health_url = server_url.rstrip("/") + "/health"
    try:
        proc = _adb(
            serial,
            "shell",
            f"curl -fsS --max-time 4 {shlex.quote(health_url)}",
            check=False,
        )
    except subprocess.SubprocessError:
        return False
    return proc.returncode == 0 and '"ok":true' in proc.stdout


def accessibility_service_enabled(
    serial: str, package: str = DEFAULT_PACKAGE
) -> bool:
    """True iff Zwangli's AccessibilityService is in the system's
    enabled list. Required for any verb that emits an
    ``accessibility_global`` client_action (home/back/recents/…)."""
    try:
        proc = _adb(
            serial, "shell", "settings", "get", "secure",
            "enabled_accessibility_services",
        )
    except subprocess.SubprocessError:
        return False
    enabled = proc.stdout.strip()
    return f"{package}/" in enabled


def intent_uri_resolvable(serial: str, *uris: str) -> bool:
    """True iff at least one of the given URIs has an
    ``android.intent.action.VIEW`` handler on the device. Used to gate
    intent-fan-out verbs (navigate, open_url) that depend on a
    third-party app the bare Genymotion image doesn't bundle."""
    for uri in uris:
        try:
            proc = _adb(
                serial,
                "shell",
                "cmd",
                "package",
                "resolve-activity",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                uri,
            )
        except subprocess.SubprocessError:
            continue
        out = proc.stdout
        # ``resolve-activity`` prints "No activity found" when nothing
        # handles the intent; otherwise it dumps a ResolveInfo block
        # containing 'name=' and 'packageName='.
        if "No activity found" not in out and "name=" in out:
            return True
    return False


def ensure_unfrozen(serial: str, package: str = DEFAULT_PACKAGE) -> None:
    """Launch MainActivity once to unfreeze the package. Harmless if it
    was already running. ``-f 32`` on the broadcast removes the need for
    this in normal flows, but bringing the activity up also makes
    foreground intents (set_alarm/set_timer) visible in dumpsys."""
    _adb(
        serial,
        "shell",
        "am",
        "start",
        "-n",
        f"{package}/.MainActivity",
        check=False,
    )


_RESULT_RE = re.compile(
    r"INJECT_RESULT\s+ok=(?P<ok>true|false)\s+"
    r"clipboard=(?P<clipboard>\d+)\s+"
    r"feedback=(?P<feedback>\d+)\s+"
    r"intents=(?P<intents>\d+)\s+"
    r"global=(?P<global>\d+)\s+"
    r"unknown=(?P<unknown>\d+)\s+"
    r"output_len=(?P<output_len>\d+)"
    r"(?:\s+error=\"(?P<error>[^\"]*)\")?"
)


def _read_inject_result(
    serial: str,
    *,
    timeout_s: float,
    poll_interval_s: float = 0.5,
) -> InjectOutcome | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        proc = _adb(
            serial,
            "logcat",
            "-d",
            "-s",
            f"{RECEIVER_TAG}:I",
            check=False,
        )
        for line in proc.stdout.splitlines():
            m = _RESULT_RE.search(line)
            if not m:
                continue
            return InjectOutcome(
                ok=m["ok"] == "true",
                clipboard=int(m["clipboard"]),
                feedback=int(m["feedback"]),
                intents=int(m["intents"]),
                global_actions=int(m["global"]),
                unknown=int(m["unknown"]),
                output_len=int(m["output_len"]),
                error=m["error"],
                raw_log_line=line,
            )
        time.sleep(poll_interval_s)
    return None


def inject_transcript(
    transcript: str,
    *,
    serial: str,
    server_url: str = "http://127.0.0.1:8766",
    token: str | None = None,
    package: str = DEFAULT_PACKAGE,
    timeout_s: float = 12.0,
) -> InjectOutcome:
    """Send ``transcript`` to the device-side dispatcher and return the
    parsed result line.

    Clears logcat first so the line we read is unambiguously the one
    our broadcast produced (otherwise a previous test's line would be
    matched by the polling loop). Raises if the receiver doesn't emit
    the line within ``timeout_s`` — that's almost always either the app
    being stopped (use ``ensure_unfrozen``), the dispatch URL being
    unreachable, or the debug variant not being installed.
    """
    _adb(serial, "logcat", "-c")

    args = [
        "am", "broadcast",
        "-f", _BROADCAST_FLAG,
        "-a", INJECT_ACTION,
        "--es", "transcript", transcript,
        "--es", "server_url", server_url,
    ]
    if token:
        args += ["--es", "token", token]
    args += ["-p", package]
    # quote each arg for the remote shell so spaces in the transcript
    # survive the round-trip without re-splitting
    remote = " ".join(shlex.quote(a) for a in args)
    _adb(serial, "shell", remote)

    outcome = _read_inject_result(serial, timeout_s=timeout_s)
    if outcome is None:
        proc = _adb(serial, "logcat", "-d", "-s", f"{RECEIVER_TAG}:V", check=False)
        raise RuntimeError(
            f"No INJECT_RESULT line within {timeout_s}s for "
            f"transcript={transcript!r}. Recent receiver log:\n"
            f"{proc.stdout[-2000:]}"
        )
    return outcome
