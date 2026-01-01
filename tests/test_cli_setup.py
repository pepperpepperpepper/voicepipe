from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.config import env_file_path
from voicepipe.systemd import TARGET_UNIT


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]


def _combined_cli_output(result) -> str:
    out = getattr(result, "output", "") or ""
    try:
        err = getattr(result, "stderr", "") or ""
    except ValueError:
        err = ""
    return out + err


def test_setup_writes_env_file_installs_units_and_enables_target(
    isolated_home: Path, fake_systemd: Path
) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("systemd setup is not supported on Windows/macOS")
    fake_systemd.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--api-key", "sk-test"])
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert env_path.exists()
    assert "OPENAI_API_KEY=sk-test" in env_path.read_text(encoding="utf-8")

    unit_dir = isolated_home / ".config" / "systemd" / "user"
    assert (unit_dir / TARGET_UNIT).exists()

    calls = _read_calls(fake_systemd)
    assert ["--user", "daemon-reload"] in calls
    assert ["--user", "enable", TARGET_UNIT] in calls
    assert ["--user", "restart", TARGET_UNIT] in calls


def test_setup_from_stdin_works(isolated_home: Path, fake_systemd: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        main, ["setup", "--from-stdin", "--no-systemd"], input="sk-stdin\n"
    )
    assert result.exit_code == 0, result.output

    env_path = env_file_path()
    assert "OPENAI_API_KEY=sk-stdin" in env_path.read_text(encoding="utf-8")


def test_setup_skip_key_cannot_be_combined_with_api_key(fake_systemd: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--skip-key", "--api-key", "sk-test"])
    assert result.exit_code != 0
    assert "--skip-key cannot be combined" in result.output


def test_setup_skips_systemd_on_windows(isolated_home: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only behavior")
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--api-key", "sk-test"])
    assert result.exit_code == 0, result.output
    assert "skipping systemd setup" in _combined_cli_output(result)
    assert env_file_path().exists()


def test_setup_skips_systemd_on_macos(isolated_home: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS-only behavior")
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "--api-key", "sk-test"])
    assert result.exit_code == 0, result.output
    assert "skipping systemd setup" in _combined_cli_output(result)
    assert env_file_path().exists()
