from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from voicepipe.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_validate_succeeds_on_minimal_valid_config(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {"strip": {"type": "builtin"}},
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "✓ triggers.json valid" in result.output
    assert "1 trigger" in result.output
    assert "zwingli" in result.output
    # The auto-injected help/yes/no verbs show up alongside 'strip'.
    assert "strip" in result.output
    assert "help" in result.output
    assert "yes" in result.output
    assert "no" in result.output


def test_validate_succeeds_on_default_asset(runner) -> None:
    asset = Path(__file__).resolve().parent.parent / "voicepipe" / "assets" / "triggers.default.json"
    result = runner.invoke(main, ["triggers", "validate", "--path", str(asset)])
    assert result.exit_code == 0, result.output
    assert "✓ triggers.json valid" in result.output


def test_validate_missing_file_exits_with_error(runner, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    result = runner.invoke(main, ["triggers", "validate", "--path", str(missing)])
    assert result.exit_code == 1
    assert "✗ triggers.json not found" in result.output


def test_validate_invalid_json_exits_with_error(runner, tmp_path: Path) -> None:
    cfg = tmp_path / "triggers.json"
    cfg.write_text("{ not json", encoding="utf-8")
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg)])
    assert result.exit_code == 1
    assert "✗ triggers.json invalid" in result.output
    assert "Invalid JSON" in result.output


def test_validate_structural_error_shows_useful_message(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "sub": {"type": "shell", "enabled": True, "confirm": "yes"},
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg)])
    assert result.exit_code == 1
    assert "✗ triggers.json invalid" in result.output
    assert "confirm" in result.output


def test_validate_unsupported_version_exits_with_error(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", {"version": 99, "triggers": {}})
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg)])
    assert result.exit_code == 1
    assert "✗ triggers.json invalid" in result.output


def test_strict_warns_about_dangling_profile_reference(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "foo": {"type": "llm", "profile": "missing_profile"},
            },
            "llm_profiles": {},
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "warnings (1)" in result.output
    assert "profile 'missing_profile'" in result.output


def test_strict_warns_about_codegen_interpreter_not_in_path(
    runner, tmp_path: Path, monkeypatch
) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "weird": {
                    "type": "codegen",
                    "enabled": True,
                    "interpreter": "definitely-not-installed-xyz",
                    "profile": "p",
                },
            },
            "llm_profiles": {"p": {"system_prompt": "..."}},
        },
    )
    # Make sure shutil.which would return None for our fake binary.
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "definitely-not-installed-xyz" in result.output
    assert "not found in PATH" in result.output


def test_strict_warns_about_alias_shadowing_a_verb(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "foo": {"type": "builtin"},
                "bar": {"type": "builtin", "aliases": ["foo"]},
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "shadows a verb" in result.output
    assert "'foo'" in result.output


def test_strict_warns_about_duplicate_alias_across_verbs(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "a": {"type": "builtin", "aliases": ["shared phrase"]},
                "b": {"type": "builtin", "aliases": ["shared phrase"]},
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "shared phrase" in result.output
    assert "claimed by both" in result.output


def test_strict_passes_silently_when_no_warnings(runner, tmp_path: Path, monkeypatch) -> None:
    # Use the default asset, which should produce no warnings when its
    # bash/python/perl/node interpreters are pretend-installed.
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {"strip": {"type": "builtin"}},
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 0, result.output
    assert "warnings" not in result.output
