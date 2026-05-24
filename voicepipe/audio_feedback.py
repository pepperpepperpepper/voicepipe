"""Tiny fire-and-forget sound effects for voicepipe events.

Audio cues are useful when you're voice-driving the tool and don't want to
look at the screen — a quick chime tells you the command worked, an error
tone tells you to glance at the typed output. Three events are wired up
today:

  - "success": a Zwingli dispatch completed normally
  - "error":   a Zwingli dispatch raised
  - "pending": a confirm-enabled verb stashed a command awaiting yes/no

Everything is opt-in. Set ``VOICEPIPE_AUDIO_FEEDBACK=1`` to enable; the
defaults pick up the OS's bundled system sounds (freedesktop on Linux,
``/System/Library/Sounds`` on macOS). Override any event's sound with a
file path via ``VOICEPIPE_AUDIO_FEEDBACK_SUCCESS=/path/to/file.wav``
(and ``_ERROR``, ``_PENDING``).

Playback is fire-and-forget: the function returns immediately and never
raises. If no player is found or the sound file is missing, the call is a
no-op — voicepipe's text output path is never blocked by audio.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from voicepipe.platform import is_linux, is_macos, is_windows


EVENTS: tuple[str, ...] = ("success", "error", "pending")


def _enabled() -> bool:
    raw = (os.environ.get("VOICEPIPE_AUDIO_FEEDBACK") or "").strip().lower()
    return raw in {"1", "true", "t", "yes", "y", "on"}


_LINUX_FREEDESKTOP = Path("/usr/share/sounds/freedesktop/stereo")
_MACOS_SYSTEM_SOUNDS = Path("/System/Library/Sounds")


def _default_sound_for(event: str) -> Optional[Path]:
    """Return the OS-bundled default sound for an event, or None.

    Each platform has a different sound library; pick the closest match
    semantically. If the candidate file doesn't exist (custom distro,
    minimal install), return None so the caller skips playback.
    """
    if is_linux():
        candidates = {
            "success": "complete.oga",
            "error": "dialog-error.oga",
            "pending": "dialog-information.oga",
        }
        name = candidates.get(event)
        if not name:
            return None
        path = _LINUX_FREEDESKTOP / name
        return path if path.exists() else None
    if is_macos():
        candidates = {
            "success": "Glass.aiff",
            "error": "Sosumi.aiff",
            "pending": "Tink.aiff",
        }
        name = candidates.get(event)
        if not name:
            return None
        path = _MACOS_SYSTEM_SOUNDS / name
        return path if path.exists() else None
    return None


def _override_for(event: str) -> Optional[Path]:
    env_name = f"VOICEPIPE_AUDIO_FEEDBACK_{event.upper()}"
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _resolve_sound_path(event: str) -> Optional[Path]:
    override = _override_for(event)
    if override is not None:
        return override
    return _default_sound_for(event)


_PLAYER_CANDIDATES: tuple[str, ...] = (
    "paplay",   # PulseAudio (most modern Linux)
    "aplay",    # ALSA fallback
    "afplay",   # macOS
    "ffplay",   # cross-platform via ffmpeg
    "play",     # sox
)


def _find_player() -> Optional[str]:
    for name in _PLAYER_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


def _player_argv(player: str, sound: Path) -> list[str]:
    """Build argv for a given player. ffplay needs flags to exit silently."""
    base = os.path.basename(player)
    if base == "ffplay":
        return [player, "-nodisp", "-autoexit", "-loglevel", "quiet", str(sound)]
    return [player, str(sound)]


def play(event: str) -> None:
    """Play the configured sound for `event`. Fire-and-forget; never raises.

    A no-op when ``VOICEPIPE_AUDIO_FEEDBACK`` is unset, when the event has
    no resolved sound file, or when no player is on PATH.
    """
    if not _enabled():
        return
    if event not in EVENTS:
        return
    sound = _resolve_sound_path(event)
    if sound is None:
        return
    try:
        if not sound.exists():
            return
    except Exception:
        return

    if is_windows():
        # PowerShell's [System.Media.SoundPlayer] handles .wav cleanly.
        try:
            subprocess.Popen(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(New-Object Media.SoundPlayer '{sound}').PlaySync()",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception:
            pass
        return

    player = _find_player()
    if not player:
        return
    try:
        subprocess.Popen(
            _player_argv(player, sound),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass


def available_events() -> Iterable[str]:
    """Public accessor for the supported event names (useful in tests/docs)."""
    return tuple(EVENTS)


def event_for_trigger_payload(payload: object) -> Optional[str]:
    """Map an ``apply_transcript_triggers`` payload to an audio event name.

    Returns "error" when the dispatcher reported a failure, "pending" when a
    confirm-enabled verb stashed a command awaiting yes/no, otherwise
    "success". Returns None when the input is not a trigger payload (so the
    no-match passthrough path stays silent).
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is False:
        return "error"
    meta = payload.get("meta")
    if isinstance(meta, dict):
        handler_meta = meta.get("handler_meta")
        if isinstance(handler_meta, dict) and handler_meta.get("pending") is True:
            return "pending"
    return "success"
