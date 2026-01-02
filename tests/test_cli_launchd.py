from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from voicepipe.cli import main


def _combined_cli_output(result) -> str:
    out = getattr(result, "output", "") or ""
    try:
        err = getattr(result, "stderr", "") or ""
    except ValueError:
        err = ""
    return out + err


def test_launchd_print_errors_on_non_macos(isolated_home: Path) -> None:
    if sys.platform == "darwin":
        pytest.skip("macOS-only behavior")
    runner = CliRunner()
    result = runner.invoke(main, ["launchd", "print"])
    assert result.exit_code != 0
    assert "macos" in _combined_cli_output(result).lower()


def test_launchd_print_renders_valid_plist(monkeypatch, isolated_home: Path) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    runner = CliRunner()
    result = runner.invoke(main, ["launchd", "print", "--service", "transcriber"])
    assert result.exit_code == 0, result.output

    payload = plistlib.loads(result.output.encode("utf-8"))
    assert payload["Label"] == "com.voicepipe.transcriber"
    assert payload["ProgramArguments"][1] == "-m"
    assert payload["ProgramArguments"][2] == "voicepipe.transcriber_daemon"


def test_launchd_install_writes_plist_file(monkeypatch, isolated_home: Path) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    runner = CliRunner()
    result = runner.invoke(main, ["launchd", "install", "--service", "transcriber", "--no-load"])
    assert result.exit_code == 0, result.output

    plist_path = isolated_home / "Library" / "LaunchAgents" / "com.voicepipe.transcriber.plist"
    assert plist_path.exists()

    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == "com.voicepipe.transcriber"


def test_launchd_uninstall_removes_plist_file(monkeypatch, isolated_home: Path) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    plist_path = isolated_home / "Library" / "LaunchAgents" / "com.voicepipe.transcriber.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("noop", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main, ["launchd", "uninstall", "--service", "transcriber", "--no-unload"]
    )
    assert result.exit_code == 0, result.output
    assert not plist_path.exists()

