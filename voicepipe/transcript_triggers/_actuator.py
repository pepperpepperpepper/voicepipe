"""Actuator protocol — the OS-touching boundary for the dispatcher.

The dispatcher itself is pure Python: it parses, matches, calls LLMs over
HTTP, splits chains, decides outcomes. The only places it reaches into the
host OS are:

- running subprocesses (``shell`` / ``codegen`` verbs, ``yes``-resumed pending)
- copying to the clipboard (``error_destination`` = ``clipboard`` or ``both``)
- playing audio feedback cues

The :class:`Actuator` protocol formalizes that boundary. Today's behaviour
lives in :class:`DesktopActuator`. Future Android, server, or test
environments supply their own implementation. Handlers consult
:meth:`Actuator.capabilities` for graceful skip when a verb needs something
the actuator can't do.

Typing the final output is **not** an actuator method — the dispatcher
returns ``(output_text, metadata)`` and the *caller* (transcriber daemon,
fast path) handles emission. That layer has its own platform code and is
out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


CAP_SUBPROCESS = "subprocess"
CAP_CLIPBOARD = "clipboard"
CAP_AUDIO_FEEDBACK = "audio_feedback"
CAP_WEB_SEARCH = "web_search"
CAP_OPEN_URL = "open_url"
CAP_SET_ALARM = "set_alarm"
CAP_SET_TIMER = "set_timer"
CAP_DIAL = "dial"
# Client can resolve a business name → number itself (via /resolve-call) and
# show step-by-step status, instead of the server resolving inline.
CAP_RESOLVE_DIAL = "resolve_dial"
CAP_NAVIGATE = "navigate"
CAP_ACCESSIBILITY_GLOBAL = "accessibility_global"
CAP_CALENDAR = "calendar"
CAP_EMAIL = "email"
# Client can reach a saved contact through a messaging app (WhatsApp / Signal)
# or SMS — a voice/video call or a text — resolving the contact on-device.
CAP_REACH_CONTACT = "reach_contact"
# Client can launch a named app (WhatsApp, WeChat, Telegram, …) to its home
# screen, optionally copying a contact/query to the clipboard so the user can
# paste it into the app's own search (these apps expose no search deep link).
CAP_OPEN_APP = "open_app"
# Client can open a map showing a places search ("nearby gas stations",
# "nearest pharmacy") — results near the current location, not turn-by-turn
# routing (that's `navigate`).
CAP_MAP_SEARCH = "map_search"
# Device controls.
CAP_MEDIA_CONTROL = "media_control"  # play/pause/next/previous transport
CAP_VOLUME = "volume"                # raise/lower/mute/set media volume
CAP_FLASHLIGHT = "flashlight"        # torch on/off/toggle
CAP_CAMERA = "camera"                # open the camera (photo/video/selfie)
CAP_NOTE = "note"                    # jot a note to self (Keep / share)
CAP_WEATHER = "weather"              # show the weather (optionally for a place)
CAP_DND = "dnd"                      # toggle Do Not Disturb

DND_STATES: frozenset[str] = frozenset({"on", "off"})

# Valid argument values for the device-control verbs (shared client/server).
MEDIA_ACTIONS: frozenset[str] = frozenset(
    {"play", "pause", "play_pause", "next", "previous", "stop"}
)
VOLUME_ACTIONS: frozenset[str] = frozenset(
    {"up", "down", "mute", "unmute", "set"}
)
FLASHLIGHT_STATES: frozenset[str] = frozenset({"on", "off", "toggle"})
CAMERA_MODES: frozenset[str] = frozenset({"photo", "video", "selfie"})

# Messaging platforms a `reach_contact` action may target.
REACH_PLATFORMS: frozenset[str] = frozenset({"whatsapp", "signal", "sms"})
# Modes within a platform: a voice call, a video call, or a text message.
REACH_MODES: frozenset[str] = frozenset({"call", "video", "message"})


# Whitelist of global accessibility actions the dispatcher will dispatch.
# Mirrors AccessibilityService.GLOBAL_ACTION_* on Android (BACK=1,
# HOME=2, RECENTS=3, NOTIFICATIONS=4, QUICK_SETTINGS=5). The client maps
# the string back to the constant — keeping the wire format symbolic
# means logs and tests don't depend on Android's numeric API.
ACCESSIBILITY_GLOBAL_ACTIONS: frozenset[str] = frozenset(
    {
        "back",
        "home",
        "recents",
        "notifications",
        "quick_settings",
        "screenshot",
        "lock_screen",
    }
)


class ActuatorCapabilityError(RuntimeError):
    """Raised when a handler asks the actuator to do something it can't.

    The dispatcher's standard error path catches this and routes it
    through ``dispatch.error_destination`` so the user sees a polite
    ``⚠ zwingli: …`` notice rather than a stack trace.
    """


@dataclass(frozen=True)
class SubprocessResult:
    """Stable contract for subprocess execution across actuator backends.

    Mirrors the subset of :class:`subprocess.CompletedProcess` that handlers
    care about, plus a ``timed_out`` flag so backends can report timeouts
    without raising a stdlib-specific exception type.
    """

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


@runtime_checkable
class Actuator(Protocol):
    """OS-touching boundary the dispatcher consults.

    Implementations should not raise on "capability unsupported" beyond
    :class:`ActuatorCapabilityError`. Callers may pre-check via
    :meth:`capabilities` if they want to short-circuit.

    The ``web_search`` / ``open_url`` / ``set_alarm`` / ``set_timer`` /
    ``dial`` methods are "Intent-style" verbs: on desktop they may map
    to the system browser / clock / dialer; on Android (via
    :class:`~voicepipe.dispatch_server.ServerActuator`) they queue
    ``client_actions`` for the phone to execute via the corresponding
    Android Intent. All return ``True`` if the action was carried out
    or queued, ``False`` if the actuator can't do it.
    """

    def capabilities(self) -> frozenset[str]:
        ...

    def run_subprocess(
        self,
        argv: list[str] | str,
        *,
        shell: bool = False,
        timeout_seconds: float | None = None,
    ) -> SubprocessResult:
        ...

    def set_clipboard(self, text: str) -> bool:
        ...

    def play_feedback(self, event: str) -> None:
        ...

    def web_search(self, query: str) -> bool:
        ...

    def open_url(self, url: str) -> bool:
        ...

    def set_alarm(
        self,
        hour: int | None,
        minutes: int | None,
        message: str | None = None,
        *,
        in_seconds: int | None = None,
    ) -> bool:
        ...

    def set_timer(self, seconds: int, message: str | None = None) -> bool:
        ...

    def dial(self, number: str) -> bool:
        ...

    def call_business(self, query: str) -> bool:
        """Resolve a business/place NAME to a phone number and queue a dial.

        Server-side implementations look the number up (e.g. via Serper) and
        emit a ``dial`` action; clients with no lookup capability return False.
        """
        ...

    def navigate(self, destination: str, mode: str | None = None) -> bool:
        ...

    def accessibility_global(self, action: str) -> bool:
        ...

    def set_calendar_event(
        self,
        title: str,
        *,
        in_seconds: int | None = None,
        hour: int | None = None,
        minutes: int | None = None,
        day: str | None = None,
    ) -> bool:
        """Create a calendar event. Title-only opens the composer with no time;
        a relative ``in_seconds`` or absolute ``hour``/``minutes`` (+ optional
        ``day``: today/tomorrow/<weekday>/<YYYY-MM-DD>) pre-fills the start time.
        The device resolves the wall-clock start (it knows now + timezone)."""
        ...

    def compose_email(self, to: str, subject: str, body: str) -> bool:
        ...

    def take_note(self, text: str) -> bool:
        """Jot a quick note to self — the client opens a notes app (Keep) or a
        share sheet pre-filled with ``text``."""
        ...

    def show_weather(self, location: str | None = None) -> bool:
        """Show the weather, optionally for ``location`` (else current)."""
        ...

    def set_dnd(self, state: str) -> bool:
        """Toggle Do Not Disturb: ``on`` / ``off``."""
        ...

    def reach_contact(
        self,
        name: str,
        platform: str,
        mode: str,
        body: str | None = None,
    ) -> bool:
        """Reach a saved contact by NAME through ``platform`` (whatsapp /
        signal / sms) with ``mode`` (call / video / message).

        Resolution is entirely on-device (the contact's WhatsApp/Signal
        data rows, or phone number for SMS), so server implementations just
        queue a ``reach_contact`` client action; desktop returns False.
        """
        ...

    def open_app(self, app: str, query: str | None = None) -> bool:
        """Launch a named app (``whatsapp``, ``wechat``, …) to its home
        screen. If ``query`` is given, the client copies it to the clipboard
        so the user can paste it into the app's own search. Server queues an
        ``open_app`` client action; desktop returns False.
        """
        ...

    def find_places(self, query: str) -> bool:
        """Open a map showing a places search for ``query`` near the current
        location ("gas stations", "pharmacy"). Distinct from :meth:`navigate`,
        which routes to one destination. Server queues a ``map_search`` client
        action; desktop returns False.
        """
        ...

    def media_control(self, action: str) -> bool:
        """Media transport control: ``play`` / ``pause`` / ``play_pause`` /
        ``next`` / ``previous`` / ``stop``. The client dispatches the matching
        media key to whatever app holds audio focus."""
        ...

    def set_volume(self, action: str, level: int | None = None) -> bool:
        """Media-volume control: ``up`` / ``down`` / ``mute`` / ``unmute`` /
        ``set`` (with ``level`` 0–100 for ``set``)."""
        ...

    def flashlight(self, state: str) -> bool:
        """Torch ``on`` / ``off`` / ``toggle``."""
        ...

    def open_camera(self, mode: str) -> bool:
        """Open the camera app: ``photo`` / ``video`` / ``selfie``."""
        ...


_DEFAULT_SEARCH_URL_TEMPLATE = "https://duckduckgo.com/?q={query}"


class DesktopActuator:
    """Default actuator — wraps :mod:`subprocess`, :mod:`voicepipe.clipboard`,
    and :mod:`voicepipe.audio_feedback`.

    Reaches into ``subprocess`` and ``voicepipe.clipboard`` via
    module-attribute access at call time, so existing tests that
    monkeypatch ``voicepipe.transcript_triggers.subprocess.run`` or
    ``voicepipe.clipboard.copy_to_clipboard`` continue to intercept this
    actuator's calls without modification.

    ``web_search`` and ``open_url`` route through :mod:`webbrowser`. The
    search URL is configurable via the ``search_url_template`` ctor arg
    or the ``VOICEPIPE_SEARCH_URL_TEMPLATE`` env var; the template must
    contain ``{query}`` which will be substituted with the URL-encoded
    query. Defaults to DuckDuckGo.

    ``set_alarm`` / ``set_timer`` / ``dial`` have no standard desktop
    equivalent and are intentionally not advertised in
    :meth:`capabilities`; they always return ``False`` so dispatcher
    graceful-skip surfaces a polite error rather than the verb silently
    no-op'ing.
    """

    def __init__(self, *, search_url_template: str | None = None) -> None:
        import os as _os

        template = (
            search_url_template
            or _os.environ.get("VOICEPIPE_SEARCH_URL_TEMPLATE")
            or _DEFAULT_SEARCH_URL_TEMPLATE
        )
        if "{query}" not in template:
            template = _DEFAULT_SEARCH_URL_TEMPLATE
        self._search_url_template = template

    @property
    def search_url_template(self) -> str:
        return self._search_url_template

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {
                CAP_SUBPROCESS,
                CAP_CLIPBOARD,
                CAP_AUDIO_FEEDBACK,
                CAP_WEB_SEARCH,
                CAP_OPEN_URL,
            }
        )

    def run_subprocess(
        self,
        argv: list[str] | str,
        *,
        shell: bool = False,
        timeout_seconds: float | None = None,
    ) -> SubprocessResult:
        import subprocess

        try:
            proc = subprocess.run(
                argv,
                shell=shell,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                stdin=subprocess.DEVNULL,
            )
            return SubprocessResult(
                returncode=int(proc.returncode),
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            raw_stdout: Any = getattr(e, "stdout", None)
            if raw_stdout is None:
                raw_stdout = getattr(e, "output", None)
            raw_stderr: Any = getattr(e, "stderr", None)
            stdout = raw_stdout or ""
            stderr = raw_stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            return SubprocessResult(
                returncode=None,
                stdout=str(stdout),
                stderr=str(stderr),
                timed_out=True,
            )

    def set_clipboard(self, text: str) -> bool:
        try:
            from voicepipe.clipboard import copy_to_clipboard

            ok, _err = copy_to_clipboard(text)
            return bool(ok)
        except Exception:
            return False

    def play_feedback(self, event: str) -> None:
        try:
            from voicepipe import audio_feedback

            audio_feedback.play(event)
        except Exception:
            pass

    def web_search(self, query: str) -> bool:
        if not query.strip():
            return False
        from urllib.parse import quote_plus
        url = self._search_url_template.replace("{query}", quote_plus(query))
        return self.open_url(url)

    def open_url(self, url: str) -> bool:
        if not url.strip():
            return False
        try:
            import webbrowser

            return bool(webbrowser.open(url, new=2))
        except Exception:
            return False

    def set_alarm(
        self,
        hour: int | None,
        minutes: int | None,
        message: str | None = None,
        *,
        in_seconds: int | None = None,
    ) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def set_timer(self, seconds: int, message: str | None = None) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def dial(self, number: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def call_business(self, query: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def navigate(self, destination: str, mode: str | None = None) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def accessibility_global(self, action: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def set_calendar_event(
        self,
        title: str,
        *,
        in_seconds: int | None = None,
        hour: int | None = None,
        minutes: int | None = None,
        day: str | None = None,
    ) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def compose_email(self, to: str, subject: str, body: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def take_note(self, text: str) -> bool:
        return False

    def show_weather(self, location: str | None = None) -> bool:
        return False

    def set_dnd(self, state: str) -> bool:
        return False

    def reach_contact(
        self,
        name: str,
        platform: str,
        mode: str,
        body: str | None = None,
    ) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def open_app(self, app: str, query: str | None = None) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def find_places(self, query: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def media_control(self, action: str) -> bool:
        return False

    def set_volume(self, action: str, level: int | None = None) -> bool:
        return False

    def flashlight(self, state: str) -> bool:
        return False

    def open_camera(self, mode: str) -> bool:
        return False


@dataclass
class InMemoryActuator:
    """Test/CI actuator — captures calls in lists, never touches the OS.

    Useful for: tests that don't want to monkeypatch :mod:`subprocess`;
    backends that simulate an environment (e.g. "what would happen on
    Android?"); the dispatch-server's request handling when the client's
    actuator is reachable only through a network roundtrip.

    Pass a narrower ``caps`` to exercise graceful-skip code paths.
    """

    caps: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {
                CAP_SUBPROCESS,
                CAP_CLIPBOARD,
                CAP_AUDIO_FEEDBACK,
                CAP_WEB_SEARCH,
                CAP_OPEN_URL,
                CAP_SET_ALARM,
                CAP_SET_TIMER,
                CAP_DIAL,
                CAP_NAVIGATE,
                CAP_ACCESSIBILITY_GLOBAL,
                CAP_CALENDAR,
                CAP_EMAIL,
                CAP_REACH_CONTACT,
                CAP_OPEN_APP,
                CAP_MAP_SEARCH,
                CAP_MEDIA_CONTROL,
                CAP_VOLUME,
                CAP_FLASHLIGHT,
                CAP_CAMERA,
                CAP_NOTE,
                CAP_WEATHER,
                CAP_DND,
            }
        )
    )
    subprocess_calls: list[dict[str, Any]] = field(default_factory=list)
    clipboard_calls: list[str] = field(default_factory=list)
    feedback_calls: list[str] = field(default_factory=list)
    web_search_calls: list[str] = field(default_factory=list)
    open_url_calls: list[str] = field(default_factory=list)
    set_alarm_calls: list[dict[str, Any]] = field(default_factory=list)
    set_timer_calls: list[dict[str, Any]] = field(default_factory=list)
    dial_calls: list[str] = field(default_factory=list)
    call_business_calls: list[str] = field(default_factory=list)
    navigate_calls: list[dict[str, Any]] = field(default_factory=list)
    accessibility_global_calls: list[str] = field(default_factory=list)
    calendar_event_calls: list[dict[str, Any]] = field(default_factory=list)
    email_calls: list[dict[str, str]] = field(default_factory=list)
    note_calls: list[str] = field(default_factory=list)
    weather_calls: list[str | None] = field(default_factory=list)
    dnd_calls: list[str] = field(default_factory=list)
    reach_contact_calls: list[dict[str, Any]] = field(default_factory=list)
    open_app_calls: list[dict[str, Any]] = field(default_factory=list)
    find_places_calls: list[str] = field(default_factory=list)
    media_control_calls: list[str] = field(default_factory=list)
    volume_calls: list[dict[str, Any]] = field(default_factory=list)
    flashlight_calls: list[str] = field(default_factory=list)
    camera_calls: list[str] = field(default_factory=list)
    subprocess_result: SubprocessResult = field(
        default_factory=lambda: SubprocessResult(returncode=0, stdout="", stderr="")
    )
    clipboard_ok: bool = True

    def capabilities(self) -> frozenset[str]:
        return self.caps

    def run_subprocess(
        self,
        argv: list[str] | str,
        *,
        shell: bool = False,
        timeout_seconds: float | None = None,
    ) -> SubprocessResult:
        if CAP_SUBPROCESS not in self.caps:
            raise ActuatorCapabilityError(
                "Subprocess execution is not supported on this device."
            )
        self.subprocess_calls.append(
            {"argv": argv, "shell": shell, "timeout_seconds": timeout_seconds}
        )
        return self.subprocess_result

    def set_clipboard(self, text: str) -> bool:
        if CAP_CLIPBOARD not in self.caps:
            return False
        self.clipboard_calls.append(text)
        return bool(self.clipboard_ok)

    def play_feedback(self, event: str) -> None:
        if CAP_AUDIO_FEEDBACK not in self.caps:
            return
        self.feedback_calls.append(event)

    def web_search(self, query: str) -> bool:
        if CAP_WEB_SEARCH not in self.caps:
            return False
        self.web_search_calls.append(query)
        return True

    def open_url(self, url: str) -> bool:
        if CAP_OPEN_URL not in self.caps:
            return False
        self.open_url_calls.append(url)
        return True

    def set_alarm(
        self,
        hour: int | None,
        minutes: int | None,
        message: str | None = None,
        *,
        in_seconds: int | None = None,
    ) -> bool:
        if CAP_SET_ALARM not in self.caps:
            return False
        self.set_alarm_calls.append(
            {
                "hour": hour,
                "minutes": minutes,
                "message": message,
                "in_seconds": in_seconds,
            }
        )
        return True

    def set_timer(self, seconds: int, message: str | None = None) -> bool:
        if CAP_SET_TIMER not in self.caps:
            return False
        self.set_timer_calls.append({"seconds": seconds, "message": message})
        return True

    def dial(self, number: str) -> bool:
        if CAP_DIAL not in self.caps:
            return False
        self.dial_calls.append(number)
        return True

    def call_business(self, query: str) -> bool:
        # Test double: record the query; no network. Gated on CAP_DIAL since the
        # real implementation emits a dial action.
        if CAP_DIAL not in self.caps:
            return False
        self.call_business_calls.append(query)
        return True

    def navigate(self, destination: str, mode: str | None = None) -> bool:
        if CAP_NAVIGATE not in self.caps:
            return False
        self.navigate_calls.append({"destination": destination, "mode": mode})
        return True

    def accessibility_global(self, action: str) -> bool:
        if CAP_ACCESSIBILITY_GLOBAL not in self.caps:
            return False
        if action not in ACCESSIBILITY_GLOBAL_ACTIONS:
            return False
        self.accessibility_global_calls.append(action)
        return True

    def set_calendar_event(
        self,
        title: str,
        *,
        in_seconds: int | None = None,
        hour: int | None = None,
        minutes: int | None = None,
        day: str | None = None,
    ) -> bool:
        if CAP_CALENDAR not in self.caps:
            return False
        self.calendar_event_calls.append(
            {
                "title": title,
                "in_seconds": in_seconds,
                "hour": hour,
                "minutes": minutes,
                "day": day,
            }
        )
        return True

    def compose_email(self, to: str, subject: str, body: str) -> bool:
        if CAP_EMAIL not in self.caps:
            return False
        self.email_calls.append({"to": to, "subject": subject, "body": body})
        return True

    def reach_contact(
        self,
        name: str,
        platform: str,
        mode: str,
        body: str | None = None,
    ) -> bool:
        if CAP_REACH_CONTACT not in self.caps:
            return False
        if platform not in REACH_PLATFORMS or mode not in REACH_MODES:
            return False
        self.reach_contact_calls.append(
            {"name": name, "platform": platform, "mode": mode, "body": body}
        )
        return True

    def open_app(self, app: str, query: str | None = None) -> bool:
        if CAP_OPEN_APP not in self.caps or not app.strip():
            return False
        self.open_app_calls.append({"app": app, "query": query})
        return True

    def find_places(self, query: str) -> bool:
        if CAP_MAP_SEARCH not in self.caps or not query.strip():
            return False
        self.find_places_calls.append(query)
        return True

    def media_control(self, action: str) -> bool:
        if CAP_MEDIA_CONTROL not in self.caps or action not in MEDIA_ACTIONS:
            return False
        self.media_control_calls.append(action)
        return True

    def set_volume(self, action: str, level: int | None = None) -> bool:
        if CAP_VOLUME not in self.caps or action not in VOLUME_ACTIONS:
            return False
        if action == "set" and (level is None or not 0 <= level <= 100):
            return False
        self.volume_calls.append({"action": action, "level": level})
        return True

    def flashlight(self, state: str) -> bool:
        if CAP_FLASHLIGHT not in self.caps or state not in FLASHLIGHT_STATES:
            return False
        self.flashlight_calls.append(state)
        return True

    def open_camera(self, mode: str) -> bool:
        if CAP_CAMERA not in self.caps or mode not in CAMERA_MODES:
            return False
        self.camera_calls.append(mode)
        return True

    def take_note(self, text: str) -> bool:
        if CAP_NOTE not in self.caps or not text.strip():
            return False
        self.note_calls.append(text)
        return True

    def show_weather(self, location: str | None = None) -> bool:
        if CAP_WEATHER not in self.caps:
            return False
        self.weather_calls.append(location)
        return True

    def set_dnd(self, state: str) -> bool:
        if CAP_DND not in self.caps or state not in DND_STATES:
            return False
        self.dnd_calls.append(state)
        return True


_DEFAULT_ACTUATOR: Actuator | None = None


def get_default_actuator() -> Actuator:
    """Return the process-wide :class:`DesktopActuator` singleton.

    Lazy so the package import stays cheap and so the dispatcher doesn't
    pay clipboard/audio-feedback module-resolution cost until the first
    real dispatch.
    """
    global _DEFAULT_ACTUATOR
    if _DEFAULT_ACTUATOR is None:
        _DEFAULT_ACTUATOR = DesktopActuator()
    return _DEFAULT_ACTUATOR


def resolve_actuator(actuator: Actuator | None) -> Actuator:
    """Caller-side helper: ``None`` → default desktop actuator."""
    return actuator if actuator is not None else get_default_actuator()
