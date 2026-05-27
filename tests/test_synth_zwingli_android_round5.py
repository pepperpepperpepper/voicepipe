"""Round 5 — advanced Zwingli verbs end-to-end on a real Android device.

Each case follows this shape:

  1. Synthesize a spoken phrase via ElevenLabs (committed cache; first
     run regenerates with ``python -m tests.regen_synth tests/test_synth_zwingli_android_round5.py``).
  2. Transcribe the MP3 with OpenAI gpt-4o-transcribe (same backend the
     ``test_synth_zwingli_audio.py`` round uses).
  3. Fire the resulting transcript at the on-device debug
     ``INJECT_TRANSCRIPT`` receiver via ``adb shell am broadcast``,
     pointing ``server_url`` at a dispatch instance the device can
     reach (``adb reverse`` from ``localhost:8766`` works on Genymotion
     SaaS — verified 2026-05-27 on ``nsk-android14``).
  4. Tail logcat for the ``INJECT_RESULT`` line the debug receiver
     emits and assert on the counter shape the executor returned
     (``intents``, ``global_actions``, ``clipboard``, ``feedback``,
     ``unknown``).

This exercises the full Android-side path: ``DispatchPipeline`` ->
``DispatchClient`` HTTP -> server ``apply_transcript_triggers`` ->
``ClientActionExecutor`` -> real Android intents (``ACTION_SET_ALARM``,
``ACTION_DIAL``, ``google.navigation:``, …) and real
``AccessibilityService.performGlobalAction`` calls.

Coverage choices:
  * Lexical dispatch for every intent-firing verb (alarm, timer, dial,
    navigate) and for the home / back / notifications
    accessibility-global verbs.
  * One multi-step lexical chain ("timer … then home") to prove the
    planner's chain-aggregation reaches the executor.
  * Four ``llm_route`` cases via the "hey" trigger that prove the
    server's LLM router picks the right verb from natural-language
    phrases:
    - alarm and navigate (existing intent verbs through a different
      planner)
    - quick_settings — notable because "quick settings" doesn't parse
      as a single lexical verb, so it can only arrive via the router
    - recents — gpt-4o-transcribe mis-hears "Zwingli recents" as
      "Zwingli reasons", which the LLM router recovers from given a
      phrase like "show me my recent apps"

What's NOT tested here:
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
            "GROQ_API_KEY not set; llm_route cases route through Groq.",
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


def _inject(transcript: str) -> InjectOutcome:
    return inject_transcript(
        transcript,
        serial=_adb_serial(),
        server_url=_server_url_for_device(),
    )


# ----- Tests: lexical-dispatch intent verbs -------------------------


def test_alarm_fires_set_alarm_intent() -> None:
    _stt_skip_if_no_key()
    audio = synthesize("Zwingli alarm 7:30 AM, wake up.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text
    assert "alarm" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.global_actions == 0, outcome
    assert outcome.unknown == 0, outcome
    assert outcome.feedback >= 1, outcome


def test_timer_fires_set_timer_intent() -> None:
    # Picked '90 seconds' because gpt-4o-transcribe stubbornly converts
    # 'N minutes' to 'N minute' or 'set to N minutes' (verified
    # 2026-05-27); '90 seconds' survives the round-trip verbatim and the
    # _DURATION_TOKEN_RE handles it identically to a minute-form arg.
    _stt_skip_if_no_key()
    audio = synthesize("Zwingli timer 90 seconds.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text
    assert "timer" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_dial_fires_dial_intent() -> None:
    _stt_skip_if_no_key()
    # STT typically renders the digits as "555-1234" or "5551234"; the
    # dial-verb argparser keeps digits and strips punctuation.
    audio = synthesize("Zwingli dial 555 1234.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text
    assert "dial" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_navigate_fires_navigate_intent() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_maps_app()
    audio = synthesize("Zwingli navigate to Seattle.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text
    assert "navigate" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


# ----- Tests: accessibility-global verbs ----------------------------


def test_home_fires_accessibility_global_home() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_accessibility()
    audio = synthesize("Zwingli home.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome
    assert outcome.intents == 0, outcome
    assert outcome.unknown == 0, outcome


def test_back_fires_accessibility_global_back() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_accessibility()
    audio = synthesize("Zwingli back.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


def test_recents_via_llm_route_fires_accessibility_global_recents() -> None:
    """gpt-4o-transcribe hears 'Zwingli recents' as 'Zwingli reasons'
    (verified 2026-05-27), so the lexical-dispatch path can't reach the
    recents verb from synthesized speech. The LLM router can recover —
    'show me my recent apps' is unambiguous to a planner that knows the
    verb registry."""
    _stt_skip_if_no_key()
    _skip_if_no_accessibility()
    _skip_if_no_groq()
    audio = synthesize("Hey, show me my recent apps.")
    text = _transcribe(audio)
    assert _norm(text).startswith("hey "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


def test_notifications_fires_accessibility_global_notifications() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_accessibility()
    audio = synthesize("Zwingli notifications.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.global_actions == 1, outcome


# ----- Tests: multi-step lexical chain ------------------------------


def test_chain_timer_then_home_fires_both_actions() -> None:
    """The planner's chain-aggregation is the only path that produces
    *both* an intent and a global action from one transcript via the
    lexical (non-LLM) planner. If this passes but a single-verb test
    fails, the regression is verb-specific; the planner itself is OK."""
    # See test_timer_fires_set_timer_intent for why '90 seconds' instead
    # of '1 minute'.
    _stt_skip_if_no_key()
    _skip_if_no_accessibility()
    audio = synthesize("Zwingli timer 90 seconds then home.")
    text = _transcribe(audio)
    assert _starts_with(text, "zwingli ", "zwingly "), text
    assert " then " in _norm(text) or " then" in _norm(text), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome  # set_timer
    assert outcome.global_actions == 1, outcome  # home
    assert outcome.unknown == 0, outcome


# ----- Tests: LLM-routed (hey) verbs --------------------------------


def test_llm_route_alarm_via_hey() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    audio = synthesize("Hey, set an alarm for 7 AM.")
    text = _transcribe(audio)
    # STT may render "Hey" with or without the comma; just confirm it's
    # the leading token so the trigger fires.
    assert _norm(text).startswith("hey "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_llm_route_navigate_via_hey() -> None:
    _stt_skip_if_no_key()
    _skip_if_no_groq()
    _skip_if_no_maps_app()
    audio = synthesize("Hey, navigate to Seattle.")
    text = _transcribe(audio)
    assert _norm(text).startswith("hey "), text

    outcome = _inject(text)
    assert outcome.ok, outcome
    assert outcome.intents == 1, outcome
    assert outcome.unknown == 0, outcome


def test_llm_route_quick_settings_via_hey() -> None:
    """``quick settings`` isn't a single lexical verb (the lexical
    parser would tokenize it as verb=``quick`` args=``settings`` and
    fall back to ``strip``), so this only succeeds if the LLM router
    correctly maps the natural-language phrase to the ``quick_settings``
    verb. Doubles as a regression test for the verb-registry block in
    ``_llm_route._DEFAULT_SYSTEM_PROMPT``."""
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
