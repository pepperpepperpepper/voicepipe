from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _reload_config():
    import voicepipe.config as config

    return importlib.reload(config)


def test_config_home_ignores_xdg_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    assert config.config_home() == tmp_path / ".config"


def test_env_file_path_uses_dot_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    assert config.env_file_path() == tmp_path / ".config" / "voicepipe" / "voicepipe.env"


def test_get_openai_api_key_prefers_env_var(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert config.get_openai_api_key() == "from-env"


def test_get_openai_api_key_reads_legacy_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    legacy_path = tmp_path / ".config" / "voicepipe" / "api_key"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("from-legacy\n", encoding="utf-8")
    assert config.get_openai_api_key() == "from-legacy"


def test_get_openai_api_key_raises_helpful_error(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(config.VoicepipeConfigError) as exc:
        config.get_openai_api_key(load_env=False)
    assert "voicepipe.env" in str(exc.value)


def test_upsert_env_var_writes_env_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    out_path = config.upsert_env_var("OPENAI_API_KEY", "sk-test")
    assert out_path == tmp_path / ".config" / "voicepipe" / "voicepipe.env"
    text = out_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-test" in text


def test_read_env_file_parses_basic_dotenv(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = config.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "# comment\nexport FOO=bar\nQUOTED='baz'\nEMPTY=\n", encoding="utf-8"
    )
    values = config.read_env_file(env_path)
    assert values["FOO"] == "bar"
    assert values["QUOTED"] == "baz"
    assert values["EMPTY"] == ""


def test_ensure_env_file_creates_template(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = config.ensure_env_file()
    assert env_path.exists()
    assert "Voicepipe environment config" in env_path.read_text(encoding="utf-8")
    assert config.env_file_permissions_ok(env_path) is True


def test_ensure_env_file_does_not_overwrite(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = config.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("OPENAI_API_KEY=existing\n", encoding="utf-8")
    config.ensure_env_file()
    assert env_path.read_text(encoding="utf-8") == "OPENAI_API_KEY=existing\n"


def test_get_intent_routing_enabled_defaults_true(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_INTENT_ROUTING", raising=False)
    assert config.get_intent_routing_enabled(load_env=False) is True


def test_get_intent_routing_enabled_false_values(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "0")
    assert config.get_intent_routing_enabled(load_env=False) is False
    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "false")
    assert config.get_intent_routing_enabled(load_env=False) is False


def test_get_intent_wake_prefixes_defaults(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_INTENT_WAKE_PREFIXES", raising=False)
    assert config.get_intent_wake_prefixes(load_env=False) == ["zwingli"]


def test_get_intent_wake_prefixes_parses_commas(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("VOICEPIPE_INTENT_WAKE_PREFIXES", "  foo, bar,, baz ,")
    assert config.get_intent_wake_prefixes(load_env=False) == ["foo", "bar", "baz"]


def test_get_intent_wake_prefixes_empty_string_means_none(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("VOICEPIPE_INTENT_WAKE_PREFIXES", "")
    assert config.get_intent_wake_prefixes(load_env=False) == []


def test_intent_settings_from_config_file_are_used_when_env_unset(
    tmp_path: Path, monkeypatch
) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VOICEPIPE_INTENT_WAKE_PREFIXES", raising=False)
    monkeypatch.delenv("VOICEPIPE_INTENT_ROUTING", raising=False)

    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        "[intent]\nrouting_enabled = false\nwake_prefixes = [\"foo\", \"bar\"]\n",
        encoding="utf-8",
    )

    assert config.get_intent_routing_enabled() is False
    assert config.get_intent_wake_prefixes() == ["foo", "bar"]


def test_intent_env_vars_override_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        "[intent]\nrouting_enabled = false\nwake_prefixes = [\"foo\"]\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("VOICEPIPE_INTENT_ROUTING", "1")
    monkeypatch.setenv("VOICEPIPE_INTENT_WAKE_PREFIXES", "bar,baz")

    assert config.get_intent_routing_enabled() is True
    assert config.get_intent_wake_prefixes() == ["bar", "baz"]


def test_zwingli_settings_from_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_MODEL", raising=False)
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_TEMPERATURE", raising=False)
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_USER_PROMPT", raising=False)
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_SYSTEM_PROMPT", raising=False)

    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        "[zwingli]\nbackend = \"openai\"\nmodel = \"gpt-test\"\ntemperature = 0.25\nuser_prompt = \"user\"\nsystem_prompt = \"test\"\n",
        encoding="utf-8",
    )

    assert config.get_zwingli_backend() == "openai"
    assert config.get_zwingli_model() == "gpt-test"
    assert config.get_zwingli_temperature() == 0.25
    assert config.get_zwingli_user_prompt() == "user"
    assert config.get_zwingli_system_prompt() == "test"


def test_get_zwingli_backend_defaults_groq(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_BACKEND", raising=False)
    assert config.get_zwingli_backend(load_env=False) == "groq"


def test_get_zwingli_backend_env_overrides_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))

    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("[zwingli]\nbackend = \"openai\"\n", encoding="utf-8")

    monkeypatch.setenv("VOICEPIPE_ZWINGLI_BACKEND", "groq")
    assert config.get_zwingli_backend() == "groq"


def test_get_zwingli_base_url_defaults_to_groq_endpoint(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_BACKEND", raising=False)
    assert config.get_zwingli_base_url(load_env=False) == "https://api.groq.com/openai/v1"


def test_get_zwingli_base_url_can_be_set_in_toml(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))

    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        "[zwingli]\nbackend = \"groq\"\nbase_url = \"http://example.test/v1\"\n",
        encoding="utf-8",
    )

    assert config.get_zwingli_base_url() == "http://example.test/v1"


def test_get_groq_api_key_prefers_env_var(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert config.get_groq_api_key(load_env=False) == "gsk-test"


def test_get_groq_api_key_raises_helpful_error(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(config.VoicepipeConfigError) as exc:
        config.get_groq_api_key(load_env=False)
    assert "voicepipe.env" in str(exc.value)


def test_error_reporting_settings_from_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("VOICEPIPE_ERROR_REPORTING", raising=False)

    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("[errors]\nreporting_enabled = false\n", encoding="utf-8")

    assert config.get_error_reporting_enabled() is False


def test_error_reporting_env_overrides_config_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_path = tmp_path / ".config" / "voicepipe" / "config.toml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text("[errors]\nreporting_enabled = false\n", encoding="utf-8")

    monkeypatch.setenv("VOICEPIPE_ERROR_REPORTING", "1")
    assert config.get_error_reporting_enabled() is True
