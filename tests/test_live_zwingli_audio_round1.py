from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

import voicepipe.config as config
import voicepipe.transcript_triggers as tt
from voicepipe.transcription import transcribe_audio_file


pytestmark = [pytest.mark.live, pytest.mark.audio]


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize(text: str) -> str:
    lowered = (text or "").lower()
    cleaned = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(cleaned.split()).strip()


def _token_list(text: str) -> list[str]:
    cleaned = _normalize(text)
    return cleaned.split() if cleaned else []


def _contains_ordered_tokens(text: str, tokens: list[str]) -> bool:
    words = _token_list(text)
    i = 0
    for token in tokens:
        want = (token or "").strip().lower()
        if not want:
            continue
        while i < len(words) and words[i] != want:
            i += 1
        if i >= len(words):
            return False
        i += 1
    return True


def _assert_contains_ordered_tokens(text: str, tokens: list[str], *, label: str) -> None:
    if not _contains_ordered_tokens(text, tokens):
        raise AssertionError(
            f"{label} missing expected tokens: {tokens!r}\n\nraw:\n{text}"
        )


def _assert_contains_any_ordered_tokens(
    text: str, variants: list[list[str]], *, label: str
) -> None:
    if any(_contains_ordered_tokens(text, tokens) for tokens in variants):
        return
    raise AssertionError(
        f"{label} missing expected token variants: {variants!r}\n\nraw:\n{text}"
    )


def _asset_path(*parts: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / "zwingli_round1" / Path(*parts)


def _skip_unless_live_enabled() -> None:
    if not _env_flag("VOICEPIPE_LIVE_TESTS"):
        pytest.skip(
            "Live audio tests are opt-in. Set VOICEPIPE_LIVE_TESTS=1 to run.",
            allow_module_level=True,
        )


_skip_unless_live_enabled()


def _normalize_backend(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"xi", "eleven", "eleven-labs"}:
        return "elevenlabs"
    return raw


def _resolve_live_backend_and_model() -> tuple[str, str]:
    configured_backend = _normalize_backend(config.get_transcribe_backend(load_env=True))
    raw_model = (os.environ.get("VOICEPIPE_LIVE_TRANSCRIBE_MODEL") or "").strip()

    if not raw_model:
        raw_model = config.get_transcribe_model(load_env=True)

    if ":" in raw_model:
        maybe_backend, _sep, rest = raw_model.partition(":")
        return _normalize_backend(maybe_backend), rest.strip()

    return configured_backend, raw_model


_COMMANDS = config.TranscriptCommandsConfig(
    triggers={
        "zwingli": "dispatch",
        "zwingly": "dispatch",
        # Common gpt-4o-transcribe mis-transcriptions (user recordings).
        "swingly": "dispatch",
        "swingy": "dispatch",
        "swing your": "dispatch",
        "swing the": "dispatch",
        "swing this trip": "dispatch",
        "zwingle": "dispatch",
        "zwinglistrep": "dispatch",
    },
    dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
    verbs={
        "strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin")
    },
)


_CASES = [
    {
        "file": "alpha_bravo_charlie_delta.wav",
        "expect_trigger": False,
        "output_tokens": ["alpha", "bravo", "charlie", "delta"],
    },
    {
        "file": "please_ignore_this_semicolon_zwingli_strip_alpha_bravo_charlie.wav",
        # This file is intentionally a transcription-stability probe; some runs
        # start with a quoted "zwingle strip ...", others lose the trigger phrase.
        # Assert only that we get the content words out.
        "trigger_optional": True,
        "output_tokens": ["alpha", "bravo", "charlie"],
    },
    {
        "file": "zwingli.wav",
        "expect_trigger": True,
        "expect_mode": "unknown-verb",
        "output_tokens": [],
    },
    {
        "file": "zwingli_banana_alpha_bravo_charlie.wav",
        "expect_trigger": True,
        "expect_mode": "unknown-verb",
        "output_tokens": ["banana", "alpha", "bravo", "charlie"],
    },
    {
        "file": "zwingli_bash_list_files_in_my_home_directory.wav",
        "expect_trigger": True,
        "expect_mode": "unknown-verb",
        "output_tokens": ["bash", "list", "files", "home", "directory"],
    },
    {
        "file": "zwingli_comma_strip_alpha_bravo_charlie.wav",
        "expect_trigger": True,
        "expect_mode": "verb",
        "expect_verb": "strip",
        "expect_action": "strip",
        "output_tokens": ["alpha", "bravo", "charlie"],
    },
    {
        "file": "zwingli_email_to_kelly_subject_lunch_body_are_we_still_on_for_noon.wav",
        "expect_trigger": True,
        "expect_mode": "unknown-verb",
        "output_tokens": ["email", "kelly", "subject", "lunch", "noon"],
    },
    {
        "file": "zwingli_execute_echo_hello_world.wav",
        "expect_trigger": True,
        "expect_mode": "unknown-verb",
        "output_tokens": ["execute", "echo", "hello", "world"],
    },
    {
        "file": "zwingli_rewrite_alpha_bravo_charlie.wav",
        # This round keeps LLM verbs disabled; the audio fixture is still useful
        # to detect when the master trigger is dropped by transcription.
        "expect_trigger": False,
        "output_tokens": ["rewrite", "alpha", "bravo", "charlie"],
    },
    {
        "file": "zwingli_strip_alpha_bravo_charlie.wav",
        "expect_trigger": True,
        "output_tokens": ["alpha", "bravo", "charlie"],
    },
    {
        "file": "zwingli_strip_one_two_three_four_five.wav",
        "expect_trigger": True,
        "output_token_variants": [["one", "two", "three", "four", "five"], ["12345"]],
    },
    {
        "file": "zwingly_strip_alpha_bravo_charlie.wav",
        "expect_trigger": True,
        "output_tokens": ["alpha", "bravo", "charlie"],
    },
]


@pytest.mark.parametrize("case", _CASES, ids=lambda c: c["file"])
def test_live_zwingli_audio_round1(case: dict) -> None:
    backend, model_id = _resolve_live_backend_and_model()
    model = f"{backend}:{model_id}" if model_id else backend

    if backend == "openai":
        if not config.detect_openai_api_key(load_env=True):
            pytest.skip(
                "OPENAI_API_KEY not configured (required for live transcription tests)."
            )
    elif backend == "elevenlabs":
        if not config.detect_elevenlabs_api_key(load_env=True):
            pytest.skip(
                "ELEVENLABS_API_KEY/XI_API_KEY not configured (required for live transcription tests)."
            )
    else:
        pytest.skip(f"Unsupported transcription backend for live tests: {backend!r}")

    audio = _asset_path(str(case["file"]))
    assert audio.exists(), f"Audio fixture missing: {audio}"

    text = transcribe_audio_file(
        str(audio),
        model=model,
        language="en",
        temperature=0.0,
        prefer_daemon=False,
        apply_triggers=False,
    )

    out, meta = tt.apply_transcript_triggers(text, commands=_COMMANDS)
    if bool(case.get("trigger_optional")):
        output_tokens = list(case.get("output_tokens") or [])
        output_variants = case.get("output_token_variants")
        if output_variants:
            _assert_contains_any_ordered_tokens(out, output_variants, label="output")
        elif output_tokens:
            _assert_contains_ordered_tokens(out, output_tokens, label="output")
        else:
            assert _normalize(out) == ""
        return
    if not bool(case.get("expect_trigger")):
        assert meta is None
        output_tokens = list(case.get("output_tokens") or [])
        output_variants = case.get("output_token_variants")
        if output_variants:
            _assert_contains_any_ordered_tokens(out, output_variants, label="output")
        elif output_tokens:
            _assert_contains_ordered_tokens(out, output_tokens, label="output")
        else:
            assert _normalize(out) == ""
        return

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(_COMMANDS.triggers.keys())

    normalized_out = _normalize(out)
    for t in _COMMANDS.triggers.keys():
        normalized_trigger = _normalize(t)
        if normalized_trigger and normalized_out.startswith(normalized_trigger + " "):
            raise AssertionError(f"output unexpectedly starts with trigger {t!r}: {out!r}")

    inner = meta.get("meta") or {}
    expect_mode = case.get("expect_mode")
    if expect_mode:
        assert inner.get("mode") == expect_mode

    expect_verb = case.get("expect_verb")
    if expect_verb:
        assert inner.get("verb") == expect_verb

    expect_action = case.get("expect_action")
    if expect_action:
        assert inner.get("action") == expect_action

    output_tokens = list(case.get("output_tokens") or [])
    output_variants = case.get("output_token_variants")
    if output_variants:
        _assert_contains_any_ordered_tokens(out, output_variants, label="output")
        return
    if output_tokens:
        _assert_contains_ordered_tokens(out, output_tokens, label="output")
    else:
        assert _normalize(out) == ""
