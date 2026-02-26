from __future__ import annotations

import json
import os
import re
from functools import lru_cache
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


def _asset_path(*parts: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / "zwingli_round1" / Path(*parts)


def _asset_path_round2(*parts: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / "zwingli_round2" / Path(*parts)


def _load_manifest_spoken_texts(manifest_path: Path) -> dict[str, str]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    samples = payload.get("samples")
    if not isinstance(samples, list):
        return {}

    out: dict[str, str] = {}
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        file = (sample.get("file") or "").strip()
        spoken = (sample.get("spoken_text") or "").strip()
        if file and spoken:
            out[file] = spoken
    return out


_ROUND1_SPOKEN_TEXT = _load_manifest_spoken_texts(_asset_path("manifest.json"))
_ROUND2_SPOKEN_TEXT = _load_manifest_spoken_texts(_asset_path_round2("manifest.json"))


def _prompt_hint(spoken_text: str | None) -> str | None:
    cleaned = (spoken_text or "").strip()
    if not cleaned:
        return None
    return f"The speaker may say: {cleaned}"


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


def _assert_stripped_output_does_not_start_with_trigger(
    out: str, *, commands: config.TranscriptCommandsConfig
) -> None:
    normalized_out = _normalize(out)
    for t in commands.triggers.keys():
        normalized_trigger = _normalize(t)
        if normalized_trigger and normalized_out.startswith(normalized_trigger + " "):
            raise AssertionError(f"output unexpectedly starts with trigger {t!r}: {out!r}")


_TRIGGERS = {
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
}


@lru_cache(maxsize=None)
def _transcribe_round2_audio(path: Path, *, prompt: str | None = None) -> str:
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

    return transcribe_audio_file(
        str(path),
        model=model,
        language="en",
        prompt=prompt,
        temperature=0.0,
        prefer_daemon=False,
        apply_triggers=False,
    )


_STRIP_ONLY_COMMANDS = config.TranscriptCommandsConfig(
    triggers=dict(_TRIGGERS),
    dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
    verbs={
        "strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin")
    },
)


@pytest.mark.parametrize(
    "case",
    [
        {
            "file": "zwingli_colon_strip_alpha_bravo_charlie.wav",
            "expect_mode": "verb",
            "expect_verb": "strip",
            "expect_action": "strip",
            "output_tokens": ["alpha", "bravo", "charlie"],
        },
        {
            "file": "zwingli_semicolon_strip_alpha_bravo_charlie.wav",
            "expect_mode": "verb",
            "expect_verb": "strip",
            "expect_action": "strip",
            "output_tokens": ["alpha", "bravo", "charlie"],
        },
        {
            "file": "zwingli_period_strip_alpha_bravo_charlie.wav",
            "expect_mode": "verb",
            "expect_verb": "strip",
            "expect_action": "strip",
            "output_tokens": ["alpha", "bravo", "charlie"],
        },
        {
            "file": "zwingli_strip_colon_alpha_bravo_charlie.wav",
            "expect_mode": "verb",
            "expect_verb": "strip",
            "expect_action": "strip",
            "output_tokens": ["alpha", "bravo", "charlie"],
        },
        {
            "file": "zwingli_strip_semicolon_alpha_bravo_charlie.wav",
            "expect_mode": "verb",
            "expect_verb": "strip",
            "expect_action": "strip",
            "output_tokens": ["alpha", "bravo", "charlie"],
        },
        {
            "file": "zwingli_bash_colon_list_files_in_my_home_directory.wav",
            "expect_mode": "unknown-verb",
            "expect_verb": "bash",
            "expect_action": "strip",
            "output_tokens": ["bash", "list", "files", "home", "directory"],
        },
        {
            "file": "zwingli_email_colon_to_kelly_subject_lunch_body_are_we_still_on_for_noon.wav",
            "expect_mode": "unknown-verb",
            "expect_verb": "email",
            "expect_action": "strip",
            "output_tokens": ["email", "kelly", "subject", "lunch", "noon"],
        },
    ],
    ids=lambda c: c["file"],
)
def test_live_zwingli_audio_round2_separators(case: dict) -> None:
    audio = _asset_path_round2(str(case["file"]))
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND2_SPOKEN_TEXT.get(str(case["file"])))
    text = _transcribe_round2_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=_STRIP_ONLY_COMMANDS)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(_STRIP_ONLY_COMMANDS.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == case["expect_mode"]

    if case.get("expect_verb"):
        assert inner.get("verb") == case["expect_verb"]
    if case.get("expect_action"):
        assert inner.get("action") == case["expect_action"]

    _assert_stripped_output_does_not_start_with_trigger(out, commands=_STRIP_ONLY_COMMANDS)
    _assert_contains_ordered_tokens(out, list(case["output_tokens"]), label="output")


@pytest.mark.parametrize(
    "case",
    [
        {
            "file": "zwingli_bash_list_files_in_my_home_directory.wav",
            "verb": "bash",
            "expect_tokens": ["bash", "list", "files", "home", "directory"],
        },
        {
            "file": "zwingli_email_to_kelly_subject_lunch_body_are_we_still_on_for_noon.wav",
            "verb": "email",
            "expect_tokens": ["email", "kelly", "subject", "lunch", "noon"],
        },
    ],
    ids=lambda c: f"disabled:{c['verb']}",
)
def test_live_zwingli_audio_round2_disabled_llm_verbs_fall_back(case: dict) -> None:
    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "bash": config.TranscriptVerbConfig(
                action="zwingli", enabled=False, type="llm", profile="bash"
            ),
            "email": config.TranscriptVerbConfig(
                action="zwingli", enabled=False, type="llm", profile="email_draft"
            ),
        },
        llm_profiles={
            "bash": config.TranscriptLLMProfileConfig(model="gpt-5.2", temperature=0.0),
            "email_draft": config.TranscriptLLMProfileConfig(model="gpt-5.2", temperature=0.0),
        },
    )

    audio = _asset_path(str(case["file"]))
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND1_SPOKEN_TEXT.get(str(case["file"])))
    text = _transcribe_round2_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "unknown-verb"
    assert inner.get("verb") == case["verb"]
    assert inner.get("action") == "strip"
    assert inner.get("disabled_verb") == case["verb"]

    _assert_stripped_output_does_not_start_with_trigger(out, commands=commands)
    _assert_contains_ordered_tokens(out, list(case["expect_tokens"]), label="output")


def _skip_unless_live_llm_enabled() -> None:
    if not _env_flag("VOICEPIPE_LIVE_LLM_TESTS"):
        pytest.skip(
            "Live LLM tests are opt-in (extra network + cost). "
            "Set VOICEPIPE_LIVE_LLM_TESTS=1 to run."
        )


@pytest.mark.parametrize(
    "case",
    [
        {
            "file": "zwingli_bash_list_files_in_my_home_directory.wav",
            "verb": "bash",
        },
        {
            "file": "zwingli_email_to_kelly_subject_lunch_body_are_we_still_on_for_noon.wav",
            "verb": "email",
        },
    ],
    ids=lambda c: f"enabled:{c['verb']}",
)
def test_live_zwingli_audio_round2_enabled_llm_verbs(
    case: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_unless_live_llm_enabled()

    llm_backend = (os.environ.get("VOICEPIPE_LIVE_ZWINGLI_BACKEND") or "").strip().lower() or "openai"
    if llm_backend not in {"openai", "groq"}:
        pytest.skip(f"Unsupported VOICEPIPE_LIVE_ZWINGLI_BACKEND for live LLM tests: {llm_backend!r}")
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_BACKEND", llm_backend)

    zwingli_key = config.get_zwingli_api_key(load_env=True)
    if not zwingli_key:
        pytest.skip(
            "No API key configured for live LLM tests "
            "(set VOICEPIPE_ZWINGLI_API_KEY and/or GROQ_API_KEY/OPENAI_API_KEY)."
        )

    default_llm_model = "gpt-5.2" if llm_backend == "openai" else "moonshotai/kimi-k2-instruct"
    llm_model = (os.environ.get("VOICEPIPE_LIVE_ZWINGLI_MODEL") or "").strip() or default_llm_model

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "bash": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="bash"
            ),
            # Common gpt-4o-transcribe mis-transcriptions for user audio:
            # "bash" -> "batch"
            "batch": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="bash"
            ),
            # "bash list" -> "bashlist"
            "bashlist": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="bash"
            ),
            "email": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="email_draft"
            ),
        },
        llm_profiles={
            "bash": config.TranscriptLLMProfileConfig(
                model=llm_model,
                temperature=0.0,
                system_prompt=(
                    "You write safe bash scripts. Output only the script text.\n"
                    "Requirements:\n"
                    "- Start with: #!/usr/bin/env bash\n"
                    "- No markdown, no backticks.\n"
                    "- Must be read-only and non-destructive.\n"
                    "- Prefer: ls -la \"$HOME\""
                ),
                user_prompt_template="Write a bash script for: {{text}}",
            ),
            "email_draft": config.TranscriptLLMProfileConfig(
                model=llm_model,
                temperature=0.0,
                system_prompt=(
                    "Draft an email. Output ONLY 3 lines:\n"
                    "To: <recipient>\n"
                    "Subject: <subject>\n"
                    "Body: <body>\n"
                    "No markdown, no extra lines."
                ),
                user_prompt_template="Draft an email from this phrase: {{text}}",
            ),
        },
    )

    audio = _asset_path(str(case["file"]))
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND1_SPOKEN_TEXT.get(str(case["file"])))
    text = _transcribe_round2_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "verb"
    if case["verb"] == "bash":
        assert inner.get("verb") in {"bash", "batch", "bashlist"}
    else:
        assert inner.get("verb") == case["verb"]
    assert inner.get("action") == "zwingli"
    assert inner.get("profile_found") is True
    assert inner.get("template_applied") is True

    handler_meta = inner.get("handler_meta") or {}
    assert handler_meta.get("model") == llm_model

    _assert_stripped_output_does_not_start_with_trigger(out, commands=commands)
    assert out.strip()

    if case["verb"] == "bash":
        assert out.lstrip().startswith("#!/usr/bin/env bash")
        assert "ls" in out
        return

    assert case["verb"] == "email"
    normalized = _normalize(out)
    assert "to" in normalized
    assert "subject" in normalized
    assert "body" in normalized
