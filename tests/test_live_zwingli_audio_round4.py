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
        raise AssertionError(f"{label} missing expected tokens: {tokens!r}\n\nraw:\n{text}")


def _asset_path_round4(*parts: str) -> Path:
    return Path(__file__).resolve().parent / "assets" / "zwingli_round4" / Path(*parts)


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


_ROUND4_SPOKEN_TEXT = _load_manifest_spoken_texts(_asset_path_round4("manifest.json"))


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


@lru_cache(maxsize=None)
def _transcribe_round4_audio(path: Path, *, prompt: str | None = None) -> str:
    backend, model_id = _resolve_live_backend_and_model()
    model = f"{backend}:{model_id}" if model_id else backend

    if backend == "openai":
        if not config.detect_openai_api_key(load_env=True):
            pytest.skip("OPENAI_API_KEY not configured (required for live transcription tests).")
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


@pytest.mark.parametrize(
    "audio_file",
    [
        "zwingli_plugin_alpha_bravo_charlie.wav",
        "zwingli_plugin_comma_alpha_bravo_charlie.wav",
    ],
    ids=lambda f: f,
)
def test_live_zwingli_audio_round4_plugin_verb_disabled_without_allow_flag(
    audio_file: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = _asset_path_round4(audio_file)
    assert audio.exists(), f"Audio fixture missing: {audio}"

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "ok_prefix.py").write_text(
        "def handle(text: str):\n"
        "    cleaned = (text or '').strip()\n"
        "    return (f'OK {cleaned}'.strip(), {'kind': 'ok-prefix', 'input': cleaned})\n",
        encoding="utf-8",
    )

    def _fake_config_dir(*, create: bool = False):
        del create
        return tmp_path

    monkeypatch.setattr(config, "config_dir", _fake_config_dir)
    # Explicitly disable plugins for this test (don't rely on the developer/user env).
    monkeypatch.setenv("VOICEPIPE_PLUGIN_ALLOW", "0")

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "plugin": config.TranscriptVerbConfig(
                action="plugin",
                enabled=True,
                type="plugin",
                plugin=config.TranscriptPluginConfig(
                    path="plugins/ok_prefix.py",
                    callable="handle",
                ),
            ),
        },
    )

    prompt = _prompt_hint(_ROUND4_SPOKEN_TEXT.get(audio_file))
    text = _transcribe_round4_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is False
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())
    assert "VOICEPIPE_PLUGIN_ALLOW=1" in str(meta.get("error") or "")

    _assert_contains_ordered_tokens(out, ["plugin", "alpha", "bravo", "charlie"], label="output")


@pytest.mark.parametrize(
    "audio_file",
    [
        "zwingli_plugin_alpha_bravo_charlie.wav",
        "zwingli_plugin_comma_alpha_bravo_charlie.wav",
    ],
    ids=lambda f: f,
)
def test_live_zwingli_audio_round4_plugin_verb_executes_when_allowed(
    audio_file: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = _asset_path_round4(audio_file)
    assert audio.exists(), f"Audio fixture missing: {audio}"

    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "ok_prefix.py").write_text(
        "def handle(text: str):\n"
        "    cleaned = (text or '').strip()\n"
        "    return (f'OK {cleaned}'.strip(), {'kind': 'ok-prefix', 'input': cleaned})\n",
        encoding="utf-8",
    )

    def _fake_config_dir(*, create: bool = False):
        del create
        return tmp_path

    monkeypatch.setattr(config, "config_dir", _fake_config_dir)
    monkeypatch.setenv("VOICEPIPE_PLUGIN_ALLOW", "1")

    commands = config.TranscriptCommandsConfig(
        triggers=dict(_TRIGGERS),
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "plugin": config.TranscriptVerbConfig(
                action="plugin",
                enabled=True,
                type="plugin",
                plugin=config.TranscriptPluginConfig(
                    path="plugins/ok_prefix.py",
                    callable="handle",
                ),
            ),
        },
    )

    prompt = _prompt_hint(_ROUND4_SPOKEN_TEXT.get(audio_file))
    text = _transcribe_round4_audio(audio, prompt=prompt)
    out, meta = tt.apply_transcript_triggers(text, commands=commands)

    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["trigger"] in set(commands.triggers.keys())

    inner = meta.get("meta") or {}
    assert inner.get("mode") == "verb"
    assert inner.get("verb") == "plugin"
    assert inner.get("verb_type") == "plugin"
    assert inner.get("action") == "plugin"
    assert inner.get("plugin", {}).get("path") == "plugins/ok_prefix.py"
    assert inner.get("plugin", {}).get("callable") == "handle"

    handler_meta = inner.get("handler_meta") or {}
    assert isinstance(handler_meta.get("duration_ms"), int)
    assert handler_meta.get("plugin_meta", {}).get("kind") == "ok-prefix"

    # Plugin output should include the OK marker and the original content words.
    _assert_contains_ordered_tokens(out, ["ok", "alpha", "bravo", "charlie"], label="output")
