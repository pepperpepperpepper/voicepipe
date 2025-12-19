from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _reload_config():
    import voicepipe.config as config

    return importlib.reload(config)


def test_config_home_prefers_xdg_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.config_home() == tmp_path


def test_env_file_path_uses_xdg_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config.env_file_path() == tmp_path / "voicepipe" / "voicepipe.env"


def test_get_openai_api_key_prefers_env_var(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert config.get_openai_api_key() == "from-env"


def test_get_openai_api_key_reads_legacy_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy_path = tmp_path / "voicepipe" / "api_key"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("from-legacy\n", encoding="utf-8")
    assert config.get_openai_api_key() == "from-legacy"


def test_get_openai_api_key_raises_helpful_error(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(config.VoicepipeConfigError) as exc:
        config.get_openai_api_key(load_env=False)
    assert "voicepipe.env" in str(exc.value)

