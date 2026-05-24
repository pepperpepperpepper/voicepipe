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


# ---------- triggers test (dry-run dispatcher) ----------


def _basic_config(extra_verbs: dict | None = None) -> dict:
    verbs = {
        "strip": {"type": "builtin"},
        "subprocess": {"type": "shell", "enabled": True, "timeout_seconds": 10},
        "python": {
            "type": "codegen",
            "enabled": True,
            "interpreter": "python3",
            "profile": "python",
            "confirm": True,
            "aliases": ["py", "in python"],
        },
    }
    if extra_verbs:
        verbs.update(extra_verbs)
    return {
        "version": 1,
        "triggers": {"zwingli": {"action": "dispatch"}},
        "verbs": verbs,
        "llm_profiles": {
            "python": {
                "temperature": 0.0,
                "system_prompt": "You are a Python generator.",
                "user_prompt_template": "Write a Python script for: {{text}}",
            }
        },
    }


def test_test_reports_no_trigger_match(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "test", "just dictation no trigger", "--path", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "No trigger matched" in result.output


def test_test_shows_trigger_match_and_dispatch(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "test", "zwingli subprocess ls -la", "--path", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "Trigger matched:" in result.output
    assert "trigger:   zwingli" in result.output
    assert "Dispatch (1 step):" in result.output
    assert "verb:       subprocess" in result.output
    assert "would_run_shell: ls -la" in result.output


def test_test_resolves_alias(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "test", "zwingli in python count files", "--path", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "verb:       python" in result.output
    assert "args:       'count files'" in result.output


def test_test_shows_llm_prompt_preview(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "test", "zwingli python count files", "--path", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "LLM call (would be sent):" in result.output
    assert "system:" in result.output
    assert "user:" in result.output
    assert "Write a Python script for: count files" in result.output


def test_test_chain_shows_both_steps_with_pipe_marker(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main,
        ["triggers", "test", "zwingli subprocess ls then python", "--path", str(cfg)],
    )
    assert result.exit_code == 0, result.output
    assert "Dispatch (chain of 2 steps):" in result.output
    assert "Step 1" in result.output
    assert "Step 2" in result.output
    assert "piped from previous step's output" in result.output


def test_test_json_output_is_parseable(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main,
        [
            "triggers",
            "test",
            "zwingli python count files",
            "--path",
            str(cfg),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["input"] == "zwingli python count files"
    assert parsed["trigger_match"]["trigger"] == "zwingli"
    assert parsed["steps"][0]["verb"] == "python"
    assert (
        parsed["steps"][0]["verb_config"]["llm_preview"]["user_prompt"]
        == "Write a Python script for: count files"
    )


def test_test_invalid_config_exits_with_error(runner, tmp_path: Path) -> None:
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not json", encoding="utf-8")
    result = runner.invoke(
        main, ["triggers", "test", "zwingli anything", "--path", str(cfg)]
    )
    assert result.exit_code == 1
    assert "✗ triggers.json invalid" in result.output


def test_test_missing_config_exits_with_error(runner, tmp_path: Path) -> None:
    result = runner.invoke(
        main, ["triggers", "test", "zwingli anything", "--path", str(tmp_path / "missing.json")]
    )
    assert result.exit_code == 1
    assert "✗ triggers.json not found" in result.output
