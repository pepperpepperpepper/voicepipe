from __future__ import annotations

import json
import os
import re
import shutil
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


def _asset_path_round3(*parts: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / "zwingli_round3" / Path(*parts)


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


_ROUND3_SPOKEN_TEXT = _load_manifest_spoken_texts(_asset_path_round3("manifest.json"))


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


def _assert_output_does_not_start_with_trigger(out: str, *, commands: config.TranscriptCommandsConfig) -> None:
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
def _transcribe_round3_audio(path: Path, *, prompt: str | None = None) -> str:
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


def test_live_zwingli_audio_round3_execute_disabled_falls_back() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="shell",
                enabled=False,
                type="execute",
            )
        },
    )

    audio = _asset_path_round3("zwingli_execute_echo_hello_world.wav")
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND3_SPOKEN_TEXT.get("zwingli_execute_echo_hello_world.wav"))
    text = _transcribe_round3_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "unknown-verb"
    assert inner.get("verb") == "execute"
    assert inner.get("action") == "strip"
    assert inner.get("disabled_verb") == "execute"

    _assert_output_does_not_start_with_trigger(out, commands=commands)
    _assert_contains_ordered_tokens(out, ["execute", "echo", "hello", "world"], label="output")


def test_live_zwingli_audio_round3_execute_enabled_but_shell_disallowed_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="execute",
            )
        },
    )

    audio = _asset_path_round3("zwingli_execute_echo_hello_world.wav")
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND3_SPOKEN_TEXT.get("zwingli_execute_echo_hello_world.wav"))
    text = _transcribe_round3_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is False
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())
    assert "VOICEPIPE_SHELL_ALLOW=1" in str(meta.get("error") or "")

    _assert_output_does_not_start_with_trigger(out, commands=commands)
    _assert_contains_ordered_tokens(out, ["execute", "echo", "hello", "world"], label="output")


def _skip_unless_live_shell_enabled() -> None:
    if not _env_flag("VOICEPIPE_LIVE_SHELL_TESTS"):
        pytest.skip(
            "Live execute tests are opt-in (spawns real subprocesses). "
            "Set VOICEPIPE_LIVE_SHELL_TESTS=1 to run."
        )


def test_live_zwingli_audio_round3_execute_enabled_shell_allowed_runs_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_unless_live_shell_enabled()
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="execute",
                timeout_seconds=2.0,
            )
        },
    )

    audio = _asset_path_round3("zwingli_execute_echo_hello_world.wav")
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND3_SPOKEN_TEXT.get("zwingli_execute_echo_hello_world.wav"))
    text = _transcribe_round3_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "verb"
    assert inner.get("verb") == "execute"
    assert inner.get("verb_type") == "execute"
    assert inner.get("action") == "shell"
    assert inner.get("timeout_seconds") == 2.0

    handler_meta = inner.get("handler_meta") or {}
    assert handler_meta.get("returncode") == 0
    assert handler_meta.get("timeout_seconds") == 2.0

    _assert_output_does_not_start_with_trigger(out, commands=commands)
    _assert_contains_ordered_tokens(out, ["hello", "world"], label="output")


def test_live_zwingli_audio_round3_execute_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _skip_unless_live_shell_enabled()
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    if shutil.which("sleep") is None:
        pytest.skip("sleep not available on PATH (needed for timeout fixture).")

    audio = _asset_path_round3("zwingli_execute_sleep_2.wav")
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND3_SPOKEN_TEXT.get("zwingli_execute_sleep_2.wav"))
    text = _transcribe_round3_audio(audio, prompt=prompt)

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="execute",
                timeout_seconds=0.2,
            )
        },
    )

    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "verb"
    assert inner.get("verb") == "execute"
    assert inner.get("verb_type") == "execute"
    assert inner.get("action") == "shell"
    assert inner.get("timeout_seconds") == 0.2

    handler_meta = inner.get("handler_meta") or {}
    assert handler_meta.get("returncode") is None
    assert handler_meta.get("error") == "timeout"
    assert handler_meta.get("timeout_seconds") == 0.2

    _assert_output_does_not_start_with_trigger(out, commands=commands)


def test_live_zwingli_audio_round3_execute_sleep_2_shell_disallowed_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the recorded `sleep 2` fixture without executing shell commands."""
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="execute",
            )
        },
    )

    audio = _asset_path_round3("zwingli_execute_sleep_2.wav")
    assert audio.exists(), f"Audio fixture missing: {audio}"

    prompt = _prompt_hint(_ROUND3_SPOKEN_TEXT.get("zwingli_execute_sleep_2.wav"))
    text = _transcribe_round3_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is False
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())
    assert "VOICEPIPE_SHELL_ALLOW=1" in str(meta.get("error") or "")

    _assert_output_does_not_start_with_trigger(out, commands=commands)
    _assert_contains_ordered_tokens(out, ["execute", "sleep", "2"], label="output")
