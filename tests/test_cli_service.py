from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.systemd import RECORDER_UNIT, TARGET_UNIT, TRANSCRIBER_UNIT


def _read_json_lines(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    out: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def test_service_install_writes_units_and_calls_daemon_reload(
    isolated_home: Path, fake_systemd: Path
) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("systemd is not supported on Windows/macOS")
    runner = CliRunner()
    result = runner.invoke(main, ["service", "install"])
    assert result.exit_code == 0, result.output

    unit_dir = isolated_home / ".config" / "systemd" / "user"
    assert (unit_dir / RECORDER_UNIT).exists()
    assert (unit_dir / TRANSCRIBER_UNIT).exists()
    assert (unit_dir / TARGET_UNIT).exists()

    calls = _read_json_lines(fake_systemd)
    assert ["--user", "daemon-reload"] in calls


def test_service_start_defaults_to_target_when_installed(
    isolated_home: Path, fake_systemd: Path
) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("systemd is not supported on Windows/macOS")
    runner = CliRunner()
    assert runner.invoke(main, ["service", "install"]).exit_code == 0

    fake_systemd.write_text("", encoding="utf-8")
    result = runner.invoke(main, ["service", "start"])
    assert result.exit_code == 0, result.output

    calls = _read_json_lines(fake_systemd)
    assert ["--user", "start", TARGET_UNIT] in calls


def test_service_uninstall_removes_unit_files(
    isolated_home: Path, fake_systemd: Path
) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("systemd is not supported on Windows/macOS")
    runner = CliRunner()
    assert runner.invoke(main, ["service", "install"]).exit_code == 0

    result = runner.invoke(main, ["service", "uninstall"])
    assert result.exit_code == 0, result.output

    unit_dir = isolated_home / ".config" / "systemd" / "user"
    assert not (unit_dir / RECORDER_UNIT).exists()
    assert not (unit_dir / TRANSCRIBER_UNIT).exists()
    assert not (unit_dir / TARGET_UNIT).exists()


def test_service_logs_uses_journalctl(fake_systemd: Path, monkeypatch) -> None:
    if sys.platform in ("win32", "darwin"):
        pytest.skip("systemd is not supported on Windows/macOS")
    runner = CliRunner()
    journal_log = fake_systemd.parent / "journalctl.log"
    monkeypatch.setenv("VOICEPIPE_TEST_JOURNALCTL_LOG", str(journal_log))

    result = runner.invoke(main, ["service", "logs", "--no-follow", "-n", "1"])
    assert result.exit_code == 0, result.output
    assert journal_log.exists()


def test_service_commands_fail_on_windows(isolated_home: Path) -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only behavior")
    runner = CliRunner()
    result = runner.invoke(main, ["service", "install"])
    assert result.exit_code != 0
    assert "systemd is not available" in (result.output + (result.stderr or "")).lower()


def test_service_commands_fail_on_macos(isolated_home: Path) -> None:
    if sys.platform != "darwin":
        pytest.skip("macOS-only behavior")
    runner = CliRunner()
    result = runner.invoke(main, ["service", "install"])
    assert result.exit_code != 0
    assert "systemd is not available" in (result.output + (result.stderr or "")).lower()
