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


def test_hotkey_install_errors_on_non_macos(isolated_home: Path) -> None:
    if sys.platform in ("darwin", "win32"):
        pytest.skip("Hotkey install is supported on macOS and Windows")
    runner = CliRunner()
    result = runner.invoke(main, ["hotkey", "install"])
    assert result.exit_code != 0
    combined = _combined_cli_output(result).lower()
    assert "macos" in combined
    assert "windows" in combined


def test_hotkey_install_writes_workflow(monkeypatch, isolated_home: Path) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    runner = CliRunner()
    result = runner.invoke(main, ["hotkey", "install", "--name", "Voicepipe Toggle", "--force"])
    assert result.exit_code == 0, result.output

    wf_dir = isolated_home / "Library" / "Services" / "Voicepipe Toggle.workflow"
    info = wf_dir / "Contents" / "Info.plist"
    doc = wf_dir / "Contents" / "document.wflow"
    assert info.exists()
    assert doc.exists()

    info_payload = plistlib.loads(info.read_bytes())
    assert info_payload["NSServices"][0]["NSMenuItem"]["default"] == "Voicepipe Toggle"

    doc_payload = plistlib.loads(doc.read_bytes())
    assert doc_payload["workflowMetaData"]["workflowTypeIdentifier"] == "com.apple.Automator.servicesMenu"
    cmd = doc_payload["actions"][0]["action"]["ActionParameters"]["COMMAND_STRING"]
    assert "-m voicepipe.fast toggle" in cmd


def test_hotkey_uninstall_removes_workflow(monkeypatch, isolated_home: Path) -> None:
    import voicepipe.platform as platform_mod

    monkeypatch.setattr(platform_mod.sys, "platform", "darwin")

    wf_dir = isolated_home / "Library" / "Services" / "Voicepipe Toggle.workflow"
    (wf_dir / "Contents").mkdir(parents=True, exist_ok=True)
    (wf_dir / "Contents" / "Info.plist").write_text("noop", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(main, ["hotkey", "uninstall", "--name", "Voicepipe Toggle"])
    assert result.exit_code == 0, result.output
    assert not wf_dir.exists()
