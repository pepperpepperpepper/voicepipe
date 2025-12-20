from __future__ import annotations

import os
import stat
from pathlib import Path

from click.testing import CliRunner

from voicepipe.cli import main


def test_config_set_openai_key_writes_env_file(isolated_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["config", "set-openai-key", "sk-test-123"])
    assert result.exit_code == 0, result.output

    env_path = isolated_home / ".config" / "voicepipe" / "voicepipe.env"
    assert env_path.exists()
    assert "OPENAI_API_KEY=sk-test-123" in env_path.read_text(encoding="utf-8")

    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600


def test_config_show_never_prints_secret(isolated_home: Path) -> None:
    env_path = isolated_home / ".config" / "voicepipe" / "voicepipe.env"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    os.chmod(env_path, 0o600)

    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "sk-secret" not in result.output
    assert "env file has OPENAI_API_KEY: True" in result.output


def test_config_migrate_from_legacy_file_deletes_legacy_when_requested(
    isolated_home: Path,
) -> None:
    legacy_path = isolated_home / ".voicepipe_api_key"
    legacy_path.write_text("sk-legacy\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["config", "migrate", "--delete-legacy"])
    assert result.exit_code == 0, result.output

    env_path = isolated_home / ".config" / "voicepipe" / "voicepipe.env"
    assert "OPENAI_API_KEY=sk-legacy" in env_path.read_text(encoding="utf-8")
    assert not legacy_path.exists()


def test_config_edit_uses_editor_env_var(isolated_home: Path, tmp_path: Path, monkeypatch) -> None:
    # Fake editor that just exits 0.
    editor = tmp_path / "editor"
    editor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(editor, 0o700)
    monkeypatch.setenv("EDITOR", str(editor))

    runner = CliRunner()
    result = runner.invoke(main, ["config", "edit"])
    assert result.exit_code == 0, result.output
    assert "restart Voicepipe" in result.output

