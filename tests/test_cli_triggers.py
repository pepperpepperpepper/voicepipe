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


# ---------- triggers show ----------


def test_show_no_args_lists_triggers_verbs_profiles(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(main, ["triggers", "show", "--path", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Triggers (1):" in result.output
    assert "zwingli -> dispatch" in result.output
    assert "Dispatch settings:" in result.output
    assert "unknown_verb: strip" in result.output
    assert "error_destination: type" in result.output
    # 3 user-defined verbs + auto-injected help/yes/no = 6
    assert "Verbs (6):" in result.output
    assert "python" in result.output
    assert "subprocess" in result.output
    assert "LLM profiles (1):" in result.output


def test_show_no_args_json_is_parseable(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(main, ["triggers", "show", "--path", str(cfg), "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["triggers"] == {"zwingli": "dispatch"}
    assert parsed["dispatch"]["unknown_verb"] == "strip"
    assert "python" in parsed["verbs"]
    assert parsed["verbs"]["python"]["interpreter"] == "python3"
    assert parsed["verbs"]["python"]["aliases"] == ["py", "in python"]
    assert "python" in parsed["profiles"]
    assert parsed["profiles"]["python"]["temperature"] == 0.0


def test_show_verb_detail_inlines_resolved_profile(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(main, ["triggers", "show", "python", "--path", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "python:" in result.output
    assert "type: codegen" in result.output
    assert "interpreter: python3" in result.output
    assert "confirm: true" in result.output
    assert "Resolved profile (python):" in result.output
    assert "You are a Python generator." in result.output
    assert "Write a Python script for: {{text}}" in result.output


def test_show_verb_detail_json(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "show", "python", "--path", str(cfg), "--json"]
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["verb"]["name"] == "python"
    assert parsed["verb"]["interpreter"] == "python3"
    assert parsed["resolved_profile"]["name"] == "python"
    assert parsed["resolved_profile"]["temperature"] == 0.0
    # Verb 'python' and profile 'python' share a name — the collision hint
    # should be surfaced in JSON too.
    assert parsed["profile_with_same_name"] == "python"


def test_show_verb_with_unresolved_profile_flags_missing(
    runner, tmp_path: Path
) -> None:
    payload = {
        "version": 1,
        "triggers": {"zwingli": {"action": "dispatch"}},
        "verbs": {
            "foo": {"type": "llm", "profile": "ghost"},
        },
        "llm_profiles": {},
    }
    cfg = _write(tmp_path / "triggers.json", payload)
    result = runner.invoke(main, ["triggers", "show", "foo", "--path", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Profile 'ghost' is referenced but not defined." in result.output


def test_show_profile_detail_when_name_only_matches_profile(
    runner, tmp_path: Path
) -> None:
    payload = {
        "version": 1,
        "triggers": {"zwingli": {"action": "dispatch"}},
        "verbs": {"strip": {"type": "builtin"}},
        "llm_profiles": {
            "summary": {
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "system_prompt": "Summarize concisely.",
            }
        },
    }
    cfg = _write(tmp_path / "triggers.json", payload)
    result = runner.invoke(main, ["triggers", "show", "summary", "--path", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "summary (LLM profile):" in result.output
    assert "model: gpt-4o-mini" in result.output
    assert "temperature: 0.1" in result.output
    assert "Summarize concisely." in result.output


def test_show_unknown_name_exits_with_did_you_mean(runner, tmp_path: Path) -> None:
    cfg = _write(tmp_path / "triggers.json", _basic_config())
    result = runner.invoke(
        main, ["triggers", "show", "nope", "--path", str(cfg)]
    )
    assert result.exit_code == 1
    assert "no verb or profile named 'nope'" in result.output
    assert "known verbs:" in result.output
    assert "known profiles:" in result.output


def test_show_missing_config_exits_with_error(runner, tmp_path: Path) -> None:
    result = runner.invoke(
        main, ["triggers", "show", "--path", str(tmp_path / "missing.json")]
    )
    assert result.exit_code == 1
    assert "✗ triggers.json not found" in result.output


# ---------- triggers log ----------


def _write_log(path: Path, events: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )
    return path


def test_log_formats_trigger_match_and_dispatch_ok(runner, tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "trigger_match",
                "ts_ms": 1779633876887,
                "trigger": "zwingli",
                "action": "dispatch",
                "text": "zwingli strip x",
            },
            {
                "event": "dispatch_ok",
                "ts_ms": 1779633876900,
                "trigger": "zwingli",
                "output_text": "x",
            },
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "trigger_match" in result.output
    assert "trigger='zwingli'" in result.output
    assert "action='dispatch'" in result.output
    assert "text='zwingli strip x'" in result.output
    assert "dispatch_ok" in result.output
    assert "output='x'" in result.output


def test_log_summarizes_action_error(runner, tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "action_error",
                "ts_ms": 1779633876900,
                "action": "shell",
                "error": "Permission denied",
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "action_error" in result.output
    assert "action='shell'" in result.output
    assert "error='Permission denied'" in result.output


def test_log_summarizes_rate_limited(runner, tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "rate_limited",
                "ts_ms": 1779633876900,
                "verb": "python",
                "retry_after_seconds": 12,
                "limit": 5,
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "rate_limited" in result.output
    assert "verb='python'" in result.output
    assert "retry_after=12s" in result.output
    assert "limit=5" in result.output


def test_log_summarizes_shell_complete(runner, tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "shell_complete",
                "ts_ms": 1779633876900,
                "returncode": 0,
                "stdout": "hello\nworld",
                "stderr": "",
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "shell_complete" in result.output
    assert "rc=0" in result.output
    # Newline in stdout becomes a space in the snippet.
    assert "stdout='hello world'" in result.output


def test_log_tail_limits_count(runner, tmp_path: Path) -> None:
    events = [
        {"event": "trigger_match", "ts_ms": 1779633876000 + i, "trigger": f"t{i}"}
        for i in range(10)
    ]
    log = _write_log(tmp_path / "z.log", events)
    result = runner.invoke(main, ["triggers", "log", "--path", str(log), "--tail", "3"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 3
    assert "trigger='t7'" in result.output
    assert "trigger='t8'" in result.output
    assert "trigger='t9'" in result.output
    assert "trigger='t0'" not in result.output


def test_log_tail_zero_shows_all(runner, tmp_path: Path) -> None:
    events = [
        {"event": "trigger_match", "ts_ms": 1779633876000 + i, "trigger": f"t{i}"}
        for i in range(5)
    ]
    log = _write_log(tmp_path / "z.log", events)
    result = runner.invoke(main, ["triggers", "log", "--path", str(log), "--tail", "0"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if ln.strip()]
    assert len(lines) == 5


def test_log_json_passthrough(runner, tmp_path: Path) -> None:
    events = [
        {
            "event": "trigger_match",
            "ts_ms": 1779633876900,
            "trigger": "zwingli",
            "action": "dispatch",
        }
    ]
    log = _write_log(tmp_path / "z.log", events)
    result = runner.invoke(
        main, ["triggers", "log", "--path", str(log), "--json"]
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output.strip())
    assert parsed["event"] == "trigger_match"
    assert parsed["trigger"] == "zwingli"


def test_log_missing_file_exits_with_error(runner, tmp_path: Path) -> None:
    result = runner.invoke(
        main, ["triggers", "log", "--path", str(tmp_path / "missing.log")]
    )
    assert result.exit_code == 1
    assert "✗ debug log not found" in result.output


def test_log_empty_file_reports_no_events(runner, tmp_path: Path) -> None:
    log = tmp_path / "z.log"
    log.write_text("", encoding="utf-8")
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "no events" in result.output


def test_log_skips_malformed_lines(runner, tmp_path: Path) -> None:
    log = tmp_path / "z.log"
    log.write_text(
        "not json\n"
        + json.dumps(
            {"event": "trigger_match", "ts_ms": 1779633876900, "trigger": "zwingli"}
        )
        + "\n"
        + "{also bad\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "trigger_match" in result.output
    assert "trigger='zwingli'" in result.output


def test_log_unknown_event_type_falls_back_to_json_dump(runner, tmp_path: Path) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "some_future_event",
                "ts_ms": 1779633876900,
                "custom_field": "value",
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "some_future_event" in result.output
    assert "custom_field" in result.output
    assert "value" in result.output
