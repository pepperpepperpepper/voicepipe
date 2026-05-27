"""Round 5 — Android Zwangli end-to-end on a real device.

**Naming:** ``zwingli`` (with *i*) is the lexical, structured trigger
word every voicepipe client speaks to invoke verb dispatch on the
server. The Android client is named **Zwangli** (with *a*); it speaks
its own trigger word ``zwangli`` (configured in
``triggers.default.json`` as ``action: llm_route``) so the LLM router
handles natural-language phrasing instead of requiring the rigid
``VERB ARGS`` lexical grammar.

Each case follows this shape:

  1. Synthesize a spoken phrase via ElevenLabs (committed cache; first
     run regenerates with ``python -m tests.regen_synth tests/test_synth_zwingli_android_round5.py``).
  2. Transcribe the MP3 with OpenAI gpt-4o-transcribe.
  3. Fire the resulting transcript at the on-device debug
     ``INJECT_TRANSCRIPT`` receiver via ``adb shell am broadcast``,
     pointing ``server_url`` at a dispatch instance the device can
     reach (``adb reverse`` from ``localhost:8766`` works on Genymotion
     SaaS — verified 2026-05-27 on ``nsk-android14``).
  4. Tail logcat for the ``INJECT_RESULT`` line the debug receiver
     emits and assert on the counter shape the executor returned.

gpt-4o-transcribe mangles ``Zwangli`` into multiple forms depending on
phrasing — observed: ``zwanglee``, ``zwangly``, ``zwongly`` — all of
which are registered in ``triggers.default.json`` as ``llm_route``
triggers. ``_ZWANGLI_PREFIXES`` below mirrors that registry so the
"trigger survived STT" sanity assertion accepts any of the variants.

This exercises the full Android path: ``DispatchPipeline`` ->
``DispatchClient`` HTTP -> server ``apply_transcript_triggers`` ->
LLM router -> ``ClientActionExecutor`` -> real Android intents
(``ACTION_SET_ALARM``, ``ACTION_DIAL``, ``google.navigation:``, …)
and real ``AccessibilityService.performGlobalAction`` calls.

**Dispatch server rate limit.** Every test fires one LLM call (the
router), and the suite runs ~11 in <15s — that punches through the
dispatcher's default 10/min Zwingli rate limit. Start the dispatch
server with ``VOICEPIPE_ZWINGLI_RATE_LIMIT_PER_MIN=0`` for the
duration of the test run, or run with ``-x`` to bail on first
rate-limit hit. A rate-limited dispatch returns
``feedback=error`` and an empty client_actions list, which surfaces as
``assert outcome.global_actions == 1`` / ``intents == 1`` failing.

What's NOT tested here:
  * Lexical dispatch (``zwingli`` trigger) — covered by
    ``test_synth_zwingli_audio.py``. Android *could* still speak
    ``zwingli VERB ARGS`` and get the same outcome (the server accepts
    both triggers), but the natural Android path is ``zwangli`` +
    natural language.
  * On-device Android ``SpeechRecognizer`` — the app uses it in the
    real mic flow, but Genymotion SaaS has no real microphone and we
    don't own the recognizer anyway. The ``INJECT_TRANSCRIPT`` path
    bypasses it.
  * The ``type`` verb — it requires an editable focused field at the
    time of injection, which is brittle to script.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from tests._android_inject import (
    InjectOutcome,
    accessibility_service_enabled,
    app_is_installed,
    device_is_available,
    dispatch_reachable_from_device,
    ensure_unfrozen,
    inject_transcript,
    intent_uri_resolvable,
)
from tests._synth import synthesize


pytestmark = [pytest.mark.synth, pytest.mark.live, pytest.mark.android]


# Observed STT renderings of "Zwangli" via gpt-4o-transcribe (2026-05-27,
# ElevenLabs Rachel voice). Each form is registered in
# triggers.default.json so the dispatch server accepts whichever STT
# happened to produce. The trailing space anchors to the trigger as a
# leading token; matching is done after _norm() collapses punctuation.
_ZWANGLI_PREFIXES = (
    "zwangli ", "zwangly ", "zwanglee ", "zwongly ", "zhuangli ",
    "swangli ", "swangly ",
)


# ----- skip-gates ---------------------------------------------------


def _live_enabled() -> bool:
    return (os.environ.get("VOICEPIPE_LIVE_TESTS") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _adb_serial() -> str:
    """Device serial under test. Defaults to the Genymotion SaaS
    instance the rest of this repo's tooling targets (``smoke.sh``); a
    different device can be selected by exporting
    ``VOICEPIPE_ANDROID_SERIAL``."""
    return (os.environ.get("VOICEPIPE_ANDROID_SERIAL") or "localhost:40137").strip()


def _server_url_for_device() -> str:
    """URL the device will hit. Defaults to the adb-reversed loopback
    on the standard dispatch port. Override via
    ``VOICEPIPE_ANDROID_DISPATCH_URL`` for tunneled / public-IP setups."""
    return (
        os.environ.get("VOICEPIPE_ANDROID_DISPATCH_URL")
        or "http://127.0.0.1:8766"
    ).strip()


def _stt_skip_if_no_key() -> None:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        pytest.skip("OPENAI_API_KEY not set; round5 needs real STT.")


def _module_setup_skip_if_unready() -> None:
    if not _live_enabled():
        pytest.skip(
            "Live Android tests are opt-in. Set VOICEPIPE_LIVE_TESTS=1.",
            allow_module_level=True,
        )
    if shutil.which("adb") is None:
        pytest.skip("adb is not on PATH.", allow_module_level=True)
    serial = _adb_serial()
    if not device_is_available(serial):
        pytest.skip(
            f"adb device {serial!r} not in 'device' state.",
            allow_module_level=True,
        )
    if not app_is_installed(serial):
        pytest.skip(
            f"dev.voicepipe.zwangli not installed on {serial!r}.",
            allow_module_level=True,
        )
    if not dispatch_reachable_from_device(serial, server_url=_server_url_for_device()):
        pytest.skip(
            f"Device cannot reach {_server_url_for_device()}/health — "
            "set up `adb reverse tcp:8766 tcp:8766` and start the "
            "dispatch server, or override VOICEPIPE_ANDROID_DISPATCH_URL.",
            allow_module_level=True,
        )
    # Unfreeze the package once for the whole module so set_alarm /
    # set_timer / navigate / dial intents have a foregrounded launcher
    # to land in. -f 32 on each broadcast is still a belt-and-braces.
    ensure_unfrozen(serial)


_module_setup_skip_if_unready()


def _skip_if_no_accessibility() -> None:
    if not accessibility_service_enabled(_adb_serial()):
        pytest.skip(
            "Zwangli AccessibilityService is not enabled in device "
            "Settings -> Accessibility. Enable it to test "
            "back/home/recents/notifications/quick_settings.",
        )


def _skip_if_no_groq() -> None:
    if not (os.environ.get("GROQ_API_KEY") or "").strip():
        pytest.skip(
            "GROQ_API_KEY not set; zwangli + hey both route through Groq.",
        )


def _skip_if_no_maps_app() -> None:
    """The bare Genymotion ``nsk-android14`` image has no maps app, so
    nothing resolves ``google.navigation:`` or ``geo:`` and
    ``fireNavigate`` returns false even though the dispatch server
    correctly emitted the navigate client_action. Skip rather than
    confuse a real navigate regression for an image-level gap."""
    if not intent_uri_resolvable(
        _adb_serial(),
        "google.navigation:q=seattle",
        "geo:0,0?q=seattle",
    ):
        pytest.skip(
            "No app on device handles google.navigation:/geo: intents — "
            "install Google Maps or Organic Maps to run the navigate tests.",
        )


# ----- STT helpers --------------------------------------------------


def _stt_model() -> str:
    return (
        os.environ.get("VOICEPIPE_TRANSCRIBE_MODEL")
        or "gpt-4o-transcribe"
    ).strip()


def _transcribe(audio_path: Path) -> str:
    from voicepipe.transcription import transcribe_audio_file
    return transcribe_audio_file(
        str(audio_path),
        model=_stt_model(),
        prefer_daemon=False,
        apply_triggers=False,
    ).strip()


def _norm(text: str) -> str:
    lowered = (text or "").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", lowered).split()).strip()


def _starts_with(transcript: str, *prefixes: str) -> bool:
    norm = _norm(transcript)
    return any(norm.startswith(p) for p in prefixes)


def _starts_with_zwangli(transcript: str) -> bool:
    return _starts_with(transcript, *_ZWANGLI_PREFIXES)


def _inject(transcript: str) -> InjectOutcome:
    return inject_transcript(
        transcript,
        serial=_adb_serial(),
        server_url=_server_url_for_device(),
    )


# ----- Tests: zwangli → llm_route, intent verbs ---------------------


def test_alarm_fires_set_alarm_intent() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    audio = synthesize("Zwangli alarm 7:30 AM, wake up.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text
    assert "alarm" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.global_actions == 0, outcome
    assert outcome.unknown == 0, outcome
    assert outcome.feedback >= 1, outcome


def test_timer_fires_set_timer_intent() -> None:
    # '90 seconds' rather than '5 minutes' to dodge the gpt-4o-transcribe
    # word-form-numbers regression tracked in
    # https://github.com/pepperpepperpepper/voicepipe/issues/11 — under
    # zwangli → llm_route the LLM normalizes word numbers anyway, but
    # keeping the digit form lets this also run via lexical zwingli for
    # cross-trigger comparison.
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    audio = synthesize("Zwangli timer 90 seconds.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text
    assert "timer" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_dial_fires_dial_intent() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    # STT typically renders the digits as "555-1234" or sometimes
    # mangles them ("555-0034" seen 2026-05-27); the LLM router strips
    # punctuation and keeps whatever digits arrived. The test asserts
    # an intent fired, not which number — the digit fidelity is an STT
    # concern not a routing one.
    audio = synthesize("Zwangli dial 555 1234.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text
    assert "dial" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_navigate_fires_navigate_intent() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_maps_app()
    audio = synthesize("Zwangli navigate to Seattle.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text
    assert "navigate" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


# ----- Tests: zwangli → llm_route, accessibility-global verbs -------


def test_home_fires_accessibility_global_home() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli home.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome
    assert outcome.intents == 0, outcome
    assert outcome.unknown == 0, outcome


def test_back_fires_accessibility_global_back() -> None:
    # "Zwangli back." in isolation got mis-heard as '"Longley back'
    # (Z dropped, leading quote inserted) — adding a trailing word
    # gives STT enough surrounding context to land on one of the
    # registered ``zw*``/``zh*``-prefix variants. 2026-05-27.
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli back please.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


def test_recents_fires_accessibility_global_recents() -> None:
    """Recents survives via ``zwangli`` + natural language even though
    ``Zwingli recents`` gets mis-heard as ``Zwingli reasons`` by
    gpt-4o-transcribe (tracked in
    https://github.com/pepperpepperpepper/voicepipe/issues/12) — the
    LLM router doesn't care about the exact verb name in the
    transcript, just the intent."""
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli show me my recent apps.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


def test_notifications_fires_accessibility_global_notifications() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli notifications.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


def test_quick_settings_fires_accessibility_global_quick_settings() -> None:
    """``quick settings`` isn't a single lexical verb, so this could
    only ever ride in via an LLM router. Under ``zwangli`` →
    ``llm_route`` the natural phrasing just works."""
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli open the quick settings panel.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome
    assert outcome.unknown == 0, outcome


# ----- Tests: multi-step plan ---------------------------------------


def test_chain_timer_then_home_fires_both_actions() -> None:
    """The LLM router emits a 2-step plan for "X then Y" phrasing —
    distinct from the lexical planner's ``" then "``-string split but
    converges on the same ``execute_plan`` path. Verified
    2026-05-27 that the planner emits
    ``[{timer,"1m 30s"},{home,""}]`` for this transcript."""
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Zwangli timer 90 seconds then home.")
    text = _transcribe(audio)
    assert _starts_with_zwangli(text), text
    assert " then" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome  # set_timer
    assert outcome.global_actions == 1, outcome  # home
    assert outcome.unknown == 0, outcome


# ----- Tests: cross-trigger sanity via the "hey" trigger ------------
#
# ``hey`` is also wired to llm_route — these tests confirm the
# zwangli-family triggers and the hey trigger converge on the same
# routing path. Useful regression coverage if zwangli ever gets a
# distinct profile down the line.


def test_hey_alarm_routes_same_as_zwangli() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    audio = synthesize("Hey, set an alarm for 7 AM.")
    text = _transcribe(audio)
    assert _norm(text).startswith("hey "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_hey_quick_settings_routes_same_as_zwangli() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_accessibility()
    audio = synthesize("Hey, open the quick settings panel.")
    text = _transcribe(audio)
    assert _norm(text).startswith("hey "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome
    assert outcome.unknown == 0, outcome
