from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.config import env_file_path


def test_config_set_openai_key_writes_env_file(isolated_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "set-openai-key", "sk-test-123"])
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert env_path.exists()
    assert "OPENAI_API_KEY=sk-test-123" in env_path.read_text(encoding="utf-8")

    if sys.platform != "win32":
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600


def test_config_set_elevenlabs_key_writes_env_file(isolated_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "set-elevenlabs-key", "el-test-123"])
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert env_path.exists()
    assert "ELEVENLABS_API_KEY=el-test-123" in env_path.read_text(encoding="utf-8")

    if sys.platform != "win32":
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600


def test_config_show_never_prints_secret(isolated_home: Path) -> None:
    env_path = env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(env_path, 0o600)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "sk-secret" not in result.output
    assert "env file has OPENAI_API_KEY: True" in result.output


def test_config_show_never_prints_elevenlabs_secret(isolated_home: Path) -> None:
    env_path = env_file_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("ELEVENLABS_API_KEY=el-secret\n", encoding="utf-8")
    if sys.platform != "win32":
        os.chmod(env_path, 0o600)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "el-secret" not in result.output
    assert "env file has ELEVENLABS_API_KEY/XI_API_KEY: True" in result.output


def test_config_migrate_from_legacy_file_deletes_legacy_when_requested(
    isolated_home: Path,
) -> None:
    legacy_path = isolated_home / ".voicepipe_api_key"
    legacy_path.write_text("sk-legacy\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["config", "migrate", "--delete-legacy"])
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert "OPENAI_API_KEY=sk-legacy" in env_path.read_text(encoding="utf-8")
    assert not legacy_path.exists()


def test_config_edit_uses_editor_env_var(isolated_home: Path, tmp_path: Path, monkeypatch) -> None:
    # Fake editor that just exits 0 (run via the current Python interpreter so it
    # works cross-platform, including Windows).
    editor_script = tmp_path / "editor.py"
    editor_script.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    monkeypatch.setenv("EDITOR", f'\"{sys.executable}\" \"{editor_script}\"')

    runner = CliRunner()
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 0, result.output
    assert "restart voicepipe" in (result.output or "").lower()
