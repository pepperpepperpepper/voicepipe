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
        self, hour: int, minutes: int, message: str | None = None
    ) -> bool:
        ...

    def set_timer(self, seconds: int, message: str | None = None) -> bool:
        ...

    def dial(self, number: str) -> bool:
        ...


class DesktopActuator:
    """Default actuator — wraps :mod:`subprocess`, :mod:`voicepipe.clipboard`,
    and :mod:`voicepipe.audio_feedback`.

    Reaches into ``subprocess`` and ``voicepipe.clipboard`` via
    module-attribute access at call time, so existing tests that
    monkeypatch ``voicepipe.transcript_triggers.subprocess.run`` or
    ``voicepipe.clipboard.copy_to_clipboard`` continue to intercept this
    actuator's calls without modification.

    ``web_search`` and ``open_url`` route through :mod:`webbrowser`.
    ``set_alarm`` / ``set_timer`` / ``dial`` have no standard desktop
    equivalent and are intentionally not advertised in
    :meth:`capabilities`; they always return ``False`` so dispatcher
    graceful-skip surfaces a polite error rather than the verb silently
    no-op'ing.
    """

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
        return self.open_url(
            f"https://duckduckgo.com/?q={quote_plus(query)}"
        )

    def open_url(self, url: str) -> bool:
        if not url.strip():
            return False
        try:
            import webbrowser

            return bool(webbrowser.open(url, new=2))
        except Exception:
            return False

    def set_alarm(
        self, hour: int, minutes: int, message: str | None = None
    ) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def set_timer(self, seconds: int, message: str | None = None) -> bool:
        # No standard desktop equivalent; not in capabilities().
        return False

    def dial(self, number: str) -> bool:
        # No standard desktop equivalent; not in capabilities().
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
        self, hour: int, minutes: int, message: str | None = None
    ) -> bool:
        if CAP_SET_ALARM not in self.caps:
            return False
        self.set_alarm_calls.append(
            {"hour": hour, "minutes": minutes, "message": message}
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
