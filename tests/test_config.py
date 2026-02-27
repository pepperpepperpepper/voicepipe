from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest


def _reload_config():
    import voicepipe.config as config

    return importlib.reload(config)


def test_config_home_ignores_xdg_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    if sys.platform == "win32":
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
        assert config.config_home() == tmp_path / "appdata"
    elif sys.platform == "darwin":
        assert config.config_home() == tmp_path / "Library" / "Application Support"
    else:
        assert config.config_home() == tmp_path / ".config"


def test_env_file_path_uses_dot_config_home(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    if sys.platform == "win32":
        assert config.env_file_path() == tmp_path / "appdata" / "voicepipe" / "voicepipe.env"
    elif sys.platform == "darwin":
        assert (
            config.env_file_path()
            == tmp_path / "Library" / "Application Support" / "voicepipe" / "voicepipe.env"
        )
    else:
        assert config.env_file_path() == tmp_path / ".config" / "voicepipe" / "voicepipe.env"


def test_get_openai_api_key_prefers_env_var(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    assert config.get_openai_api_key() == "from-env"


def test_get_openai_api_key_reads_legacy_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    legacy_path = config.config_dir() / "api_key"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("from-legacy\n", encoding="utf-8")
    assert config.get_openai_api_key() == "from-legacy"


def test_get_openai_api_key_raises_helpful_error(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    with pytest.raises(config.VoicepipeConfigError) as exc:
        config.get_openai_api_key(load_env=False)
    assert "voicepipe.env" in str(exc.value)


def test_upsert_env_var_writes_env_file(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    out_path = config.upsert_env_var("OPENAI_API_KEY", "sk-test")
    assert out_path == config.env_file_path()
    text = out_path.read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=sk-test" in text


def test_read_env_file_parses_basic_dotenv(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    env_path = config.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "# comment\nexport FOO=bar\nQUOTED='baz'\nEMPTY=\n", encoding="utf-8"
    )
    values = config.read_env_file(env_path)
    assert values["FOO"] == "bar"
    assert values["QUOTED"] == "baz"
    assert values["EMPTY"] == ""


def test_read_env_file_strips_utf8_bom(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    env_path = config.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate a UTF-8 BOM file (common when created with PowerShell/Notepad).
    env_path.write_bytes(b"\xef\xbb\xbfOPENAI_API_KEY=from-bom\n")

    values = config.read_env_file(env_path)
    assert values["OPENAI_API_KEY"] == "from-bom"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.load_environment(load_cwd_dotenv=False)
    assert os.environ.get("OPENAI_API_KEY") == "from-bom"


def test_ensure_env_file_creates_template(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    env_path = config.ensure_env_file()
    assert env_path.exists()
    assert "Voicepipe environment config" in env_path.read_text(encoding="utf-8")
    if sys.platform == "win32":
        assert config.env_file_permissions_ok(env_path) is None
    else:
        assert config.env_file_permissions_ok(env_path) is True


def test_ensure_env_file_does_not_overwrite(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    env_path = config.env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("OPENAI_API_KEY=existing\n", encoding="utf-8")
    config.ensure_env_file()
    assert env_path.read_text(encoding="utf-8") == "OPENAI_API_KEY=existing\n"


def test_ensure_triggers_json_creates_template(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.ensure_triggers_json()
    assert triggers_path.exists()
    payload = json.loads(triggers_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["dispatch"]["unknown_verb"] == "strip"
    assert payload["triggers"]["zwingli"]["action"] == "dispatch"


def test_ensure_triggers_json_does_not_overwrite(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.triggers_json_path()
    triggers_path.parent.mkdir(parents=True, exist_ok=True)
    triggers_path.write_text("{}", encoding="utf-8")

    config.ensure_triggers_json()
    assert triggers_path.read_text(encoding="utf-8") == "{}"


def test_triggers_json_path_respects_env_override(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    override = tmp_path / "repo" / "triggers.json"
    monkeypatch.setenv("VOICEPIPE_TRIGGERS_JSON", str(override))
    assert config.triggers_json_path() == override


def test_get_transcript_triggers_default(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers.get("zwingli") == "strip"


def test_get_transcript_triggers_empty_disables(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", "")
    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers == {}


def test_get_transcript_triggers_parses_mapping(monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.setenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", "zwingli=zwingli,fix=strip,raw")
    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers["zwingli"] == "zwingli"
    assert triggers["fix"] == "strip"
    assert triggers["raw"] == "strip"


def test_get_transcript_triggers_reads_triggers_json(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {
                    "Zwingli": "strip",
                    "Fix": {"action": "zwingli"},
                },
            }
        ),
        encoding="utf-8",
    )

    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers["zwingli"] == "strip"
    assert triggers["fix"] == "zwingli"


def test_get_transcript_triggers_triggers_json_empty_disables(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(json.dumps({"version": 1, "triggers": {}}), encoding="utf-8")

    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers == {}


def test_get_transcript_triggers_env_var_overrides_triggers_json(
    tmp_path: Path, monkeypatch
) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps({"version": 1, "triggers": {"file": "strip"}}), encoding="utf-8"
    )

    monkeypatch.setenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", "env=strip")
    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers == {"env": "strip"}


def test_get_transcript_triggers_invalid_triggers_json_disables(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text("{", encoding="utf-8")

    triggers = config.get_transcript_triggers(load_env=False)
    assert triggers == {}


def test_get_transcript_commands_config_reads_dispatch_and_verbs(tmp_path: Path, monkeypatch) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "dispatch": {"unknown_verb": "strip"},
                "verbs": {
                    "strip": {"type": "builtin"},
                    "rewrite": {"type": "llm", "profile": "rewrite"},
                    "execute": {"type": "shell", "enabled": False, "timeout_seconds": 5},
                },
                "llm_profiles": {
                    "Rewrite": {
                        "model": "gpt-test",
                        "temperature": 0.3,
                        "system_prompt": "You are a dictation preprocessor. Output only the final text.",
                    },
                    "bash": {
                        "system_prompt": "Write a bash script. Output only the script.",
                        "user_prompt_template": "Write a bash script for: {{text}}",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = config.get_transcript_commands_config(load_env=False)
    assert cfg.triggers["zwingli"] == "dispatch"
    assert cfg.dispatch.unknown_verb == "strip"
    assert cfg.verbs["strip"].action == "strip"
    assert cfg.verbs["strip"].enabled is True
    assert cfg.verbs["strip"].type == "builtin"
    assert cfg.verbs["rewrite"].action == "zwingli"
    assert cfg.verbs["rewrite"].profile == "rewrite"
    assert cfg.verbs["execute"].action == "shell"
    assert cfg.verbs["execute"].enabled is False
    assert cfg.verbs["execute"].timeout_seconds == 5.0
    assert cfg.llm_profiles["rewrite"].model == "gpt-test"
    assert cfg.llm_profiles["rewrite"].temperature == 0.3
    assert cfg.llm_profiles["rewrite"].system_prompt
    assert cfg.llm_profiles["bash"].user_prompt_template == "Write a bash script for: {{text}}"


def test_get_transcript_commands_config_execute_destination_defaults_to_none(
    tmp_path: Path, monkeypatch
) -> None:
    config = _reload_config()
    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "execute": {"type": "execute", "enabled": True, "timeout_seconds": 5},
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = config.get_transcript_commands_config(load_env=False)
    assert cfg.verbs["execute"].type == "execute"
    assert cfg.verbs["execute"].destination is None


def test_get_transcript_commands_config_env_var_overrides_triggers_but_uses_verbs(
    tmp_path: Path, monkeypatch
) -> None:
    config = _reload_config()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = config.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"file": {"action": "strip"}},
                "dispatch": {"unknown_verb": "strip"},
                "verbs": {"strip": {"type": "builtin"}},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", "env=dispatch")
    cfg = config.get_transcript_commands_config(load_env=False)
    assert cfg.triggers == {"env": "dispatch"}
    assert cfg.verbs["strip"].action == "strip"
