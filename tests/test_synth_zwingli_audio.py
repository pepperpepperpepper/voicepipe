"""Synthesized-voice round-trip tests for Zwingli dispatch.

Each test follows the same shape:
  1. Synthesize a spoken phrase via ElevenLabs (committed cache; first
     run requires ELEVENLABS_API_KEY, all subsequent runs and CI do
     not — only the on-disk MP3 is needed).
  2. Transcribe the MP3 with the configured OpenAI STT backend.
     (We empirically found that gpt-4o-transcribe handles "zwingli"
     cleanly without a prompt hint; supplying one actually made the
     model drop the leading trigger token entirely.)
  3. Run the transcript through Zwingli (apply_transcript_triggers or
     dry_run_dispatch, depending on whether we want to actually execute
     or just verify the dispatch trace), against the bundled default
     asset config rather than the user's local one.
  4. Assert on the output / metadata.

Tests are marked ``synth`` and only run in the zwingli-audio CI job
(which has OPENAI_API_KEY). They skip cleanly if OPENAI_API_KEY isn't
configured locally.

STT is non-deterministic at the character level, so all string
comparisons go through ``_norm`` (lowercase, alphanumeric tokens only)
and use substring/contains semantics. The default config accepts both
"zwingli" and "zwingly" — we don't pin which spelling STT produces.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests._synth import synthesize


pytestmark = pytest.mark.synth


def _norm(text: str) -> str:
    lowered = (text or "").lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", lowered).split()).strip()


def _stt_skip_if_no_key() -> None:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        pytest.skip("OPENAI_API_KEY not set; synth round-trip tests need real STT.")


def _stt_model() -> str:
    return (
        os.environ.get("VOICEPIPE_TRANSCRIBE_MODEL")
        or "gpt-4o-transcribe"
    ).strip()


def _transcribe(audio_path: Path) -> str:
    """STT-only — does not apply triggers, so the caller can route the raw
    transcript through dispatch and inspect both the output text and the
    trigger metadata. No prompt= passed: empirically the model handles
    'zwingli' without coaching, and supplying a hint biases it toward
    dropping the leading trigger token."""
    from voicepipe.transcription import transcribe_audio_file

    return transcribe_audio_file(
        str(audio_path),
        model=_stt_model(),
        prefer_daemon=False,
        apply_triggers=False,
    ).strip()


def _default_asset_commands():
    """Load the bundled default triggers.default.json (zwingli -> dispatch,
    full verb registry) so tests are independent of the user's local
    triggers.json or VOICEPIPE_TRANSCRIPT_TRIGGERS env."""
    from voicepipe.config import TranscriptCommandsConfig, validate_triggers_json

    asset = (
        Path(__file__).resolve().parents[1]
        / "voicepipe"
        / "assets"
        / "triggers.default.json"
    )
    triggers, dispatch, verbs, profiles = validate_triggers_json(path=asset)
    return TranscriptCommandsConfig(
        triggers=triggers,
        dispatch=dispatch,
        verbs=verbs,
        llm_profiles=profiles,
    )


def _starts_with_a_zwingli_trigger(text: str) -> bool:
    """The default triggers config accepts 'zwingli' and 'zwingly'. STT
    may pick either spelling — we accept both."""
    norm = _norm(text)
    return norm.startswith("zwingli ") or norm.startswith("zwingly ")


# ---------- 1. Plain dictation passes through ----------


def test_plain_dictation_passes_through_unchanged() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import apply_transcript_triggers

    audio = synthesize("Hello world.")
    transcript = _transcribe(audio)
    assert "hello world" in _norm(transcript), (
        f"STT didn't recover 'hello world' from synth audio: {transcript!r}"
    )

    commands = _default_asset_commands()
    out, meta = apply_transcript_triggers(transcript, commands=commands)
    assert meta is None, (
        f"Plain dictation shouldn't match any trigger; got meta={meta!r}"
    )
    assert "hello world" in _norm(out)


# ---------- 2. zwingli strip — extracts remainder ----------


def test_zwingli_strip_extracts_remainder() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import apply_transcript_triggers

    audio = synthesize("Zwingli strip alpha bravo.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    out, meta = apply_transcript_triggers(transcript, commands=commands)
    assert meta is not None
    assert meta["ok"] is True
    norm = _norm(out)
    # The trigger and verb tokens should be gone; the args survive.
    assert "alpha" in norm and "bravo" in norm, (
        f"Expected 'alpha bravo' in output, got: {out!r}"
    )
    assert not norm.startswith("zwingli")
    assert not norm.startswith("strip")


# ---------- 3. zwingli help — lists the verb registry ----------


def test_zwingli_help_lists_verb_registry() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import apply_transcript_triggers

    audio = synthesize("Zwingli help.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    out, meta = apply_transcript_triggers(transcript, commands=commands)
    assert meta is not None
    assert meta["ok"] is True
    # Help output's exact wording is internal, but it always names the
    # voicepipe header + at least the 'strip' verb (auto-injected) and the
    # 'help' verb (also auto-injected). Assert on a stable subset.
    norm = _norm(out)
    assert "voicepipe" in norm, f"Expected 'voicepipe' header in help output: {out!r}"
    assert "verbs" in norm
    assert "strip" in norm
    assert "help" in norm


# ---------- 4. unknown verb — did_you_mean surfaces in metadata ----------


def test_zwingli_unknown_verb_surfaces_did_you_mean() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import apply_transcript_triggers

    # "stripe" is a real word (the payment processor) so STT preserves it
    # rather than auto-correcting to "strip"; difflib still suggests "strip"
    # (similarity ~0.91, above the 0.6 cutoff).
    audio = synthesize("Zwingli stripe alpha.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    out, meta = apply_transcript_triggers(transcript, commands=commands)
    assert meta is not None
    inner = meta.get("meta") or {}
    assert inner.get("mode") == "unknown-verb", (
        f"Expected unknown-verb dispatch mode, got meta={meta!r} out={out!r}"
    )
    suggestions = inner.get("did_you_mean") or []
    assert "strip" in suggestions, (
        f"Expected 'strip' in did_you_mean, got {suggestions!r}"
    )


# ---------- 5. multi-word alias resolves to canonical verb ----------


def test_zwingli_alias_resolves_to_canonical_verb() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import dry_run_dispatch

    # "javascript" is a single-token alias of the node codegen verb in the
    # default config. Picked over python's "in python" / "use python"
    # aliases because STT mangles those ("in" -> "and", "use" -> "used");
    # "javascript" is a real word the transcriber preserves verbatim.
    # Dry-run so we verify alias resolution without invoking the LLM.
    audio = synthesize("Zwingli javascript print one.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    trace = dry_run_dispatch(transcript, commands=commands)
    assert trace.get("trigger_match") is not None
    steps = trace.get("steps") or []
    assert steps, f"Expected at least one step, got trace={trace!r}"
    step = steps[0]
    assert step.get("verb") == "node", (
        f"Expected verb 'node' via 'javascript' alias, got {step.get('verb')!r}"
    )


# ---------- 6. chain — ' then ' splits the remainder into two steps ----------


def test_zwingli_chain_splits_on_then() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import dry_run_dispatch

    audio = synthesize("Zwingli strip alpha then strip beta.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    trace = dry_run_dispatch(transcript, commands=commands)
    assert trace.get("trigger_match") is not None
    steps = trace.get("steps") or []
    assert len(steps) == 2, (
        f"Expected a 2-step chain, got {len(steps)} steps: {steps!r}"
    )
    assert steps[0].get("verb") == "strip"
    assert steps[1].get("verb") == "strip"


# ---------- 7. LLM verb routes to the LLM call preview ----------


def test_zwingli_llm_verb_emits_llm_call_preview() -> None:
    _stt_skip_if_no_key()
    from voicepipe.transcript_triggers import dry_run_dispatch

    # 'rewrite' is an llm-type verb in the default config (action=zwingli,
    # profile=rewrite). Dry-run so we don't actually call the LLM.
    audio = synthesize("Zwingli rewrite hello world.")
    transcript = _transcribe(audio)
    assert _starts_with_a_zwingli_trigger(transcript), (
        f"STT didn't preserve the zwingli trigger: {transcript!r}"
    )

    commands = _default_asset_commands()
    trace = dry_run_dispatch(transcript, commands=commands)
    assert trace.get("trigger_match") is not None
    steps = trace.get("steps") or []
    assert steps, f"Expected at least one step, got trace={trace!r}"
    step = steps[0]
    assert step.get("verb") == "rewrite", (
        f"Expected verb 'rewrite', got {step.get('verb')!r}"
    )
    cfg = step.get("verb_config") or {}
    assert "llm_preview" in cfg, (
        f"Expected llm_preview block for the rewrite verb; got config={cfg!r}"
    )
    preview = cfg["llm_preview"]
    assert preview.get("system_prompt"), "LLM call preview should have a system prompt"
    assert preview.get("user_prompt"), "LLM call preview should have a user prompt"
