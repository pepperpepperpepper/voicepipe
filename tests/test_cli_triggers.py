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


def test_strict_warns_about_disabled_verb_with_aliases(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "old_verb": {
                    "type": "builtin",
                    "enabled": False,
                    "aliases": ["legacy", "deprecated phrase"],
                },
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "old_verb" in result.output
    assert "disabled" in result.output
    assert "legacy" in result.output
    assert "never resolve" in result.output


def test_strict_warns_about_codegen_verb_missing_profile(
    runner, tmp_path: Path, monkeypatch
) -> None:
    # Pretend `lua` is on PATH so the interpreter-not-in-PATH check stays
    # silent and we're only asserting the missing-profile warning.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "lua": {
                    "type": "codegen",
                    "enabled": True,
                    "interpreter": "lua",
                    # profile intentionally absent
                },
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 2, result.output
    assert "lua" in result.output
    assert "no `profile` set" in result.output


def test_strict_skips_missing_profile_warning_for_disabled_codegen_verb(
    runner, tmp_path: Path, monkeypatch
) -> None:
    # The parser still requires `interpreter` on disabled codegen verbs,
    # so we have to set it; what we're proving is that the missing-profile
    # warning skips disabled verbs.
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "ruby": {
                    "type": "codegen",
                    "enabled": False,
                    "interpreter": "ruby",
                    # profile intentionally absent
                },
            },
        },
    )
    result = runner.invoke(main, ["triggers", "validate", "--path", str(cfg), "--strict"])
    assert result.exit_code == 0, result.output
    assert "no `profile` set" not in result.output


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
    # Real rate_limited events use `cap_per_min` (see _dispatch.py); we also
    # accept the legacy `limit` key.
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "rate_limited",
                "ts_ms": 1779633876900,
                "verb": "python",
                "retry_after_seconds": 12,
                "cap_per_min": 5,
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "rate_limited" in result.output
    assert "verb='python'" in result.output
    assert "retry_after=12s" in result.output
    assert "cap_per_min=5" in result.output


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


def test_log_follow_help_lists_flag(runner) -> None:
    result = runner.invoke(main, ["triggers", "log", "--help"])
    assert result.exit_code == 0, result.output
    assert "--follow" in result.output
    assert "-f" in result.output


@pytest.mark.timeout(10)
def test_log_follow_with_no_events_says_waiting(
    runner, tmp_path: Path, monkeypatch
) -> None:
    # Swap the follow generator for an immediate-empty one so we exercise
    # the "(no events yet … waiting …)" branch without actually entering
    # the poll loop. Earlier this test used _thread.interrupt_main() to
    # break out of the real loop, which hung indefinitely on Windows
    # under click's CliRunner — the cross-platform-safe equivalent is to
    # monkeypatch the loop away.
    log = tmp_path / "z.log"
    log.write_text("", encoding="utf-8")

    def _noop_follow(_path, *, poll_seconds=0.5):
        return iter(())

    monkeypatch.setattr(
        "voicepipe.commands.triggers._iter_follow_log", _noop_follow
    )

    result = runner.invoke(main, ["triggers", "log", "--follow", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "waiting" in result.output


@pytest.mark.timeout(10)
def test_iter_follow_log_yields_new_appended_lines(tmp_path: Path) -> None:
    from voicepipe.commands.triggers import _iter_follow_log
    import threading
    import time

    log = tmp_path / "z.log"
    log.write_text("existing\n", encoding="utf-8")

    gen = _iter_follow_log(log, poll_seconds=0.02)

    def appender():
        time.sleep(0.05)
        with open(log, "a", encoding="utf-8") as f:
            f.write("hello world\n")
            f.write("second line\n")

    t = threading.Thread(target=appender, daemon=True)
    t.start()

    first = next(gen)
    second = next(gen)
    gen.close()
    assert first == "hello world"
    assert second == "second line"


@pytest.mark.timeout(10)
def test_iter_follow_log_buffers_partial_line_until_newline(tmp_path: Path) -> None:
    from voicepipe.commands.triggers import _iter_follow_log
    import threading
    import time

    log = tmp_path / "z.log"
    log.write_text("", encoding="utf-8")

    gen = _iter_follow_log(log, poll_seconds=0.02)

    def writer():
        time.sleep(0.05)
        with open(log, "a", encoding="utf-8") as f:
            # Partial line first — should be buffered, not yielded yet.
            f.write("hel")
            f.flush()
        time.sleep(0.1)
        with open(log, "a", encoding="utf-8") as f:
            f.write("lo\n")

    t = threading.Thread(target=writer, daemon=True)
    t.start()

    line = next(gen)
    gen.close()
    assert line == "hello"


@pytest.mark.timeout(10)
def test_iter_follow_log_handles_rotation(tmp_path: Path) -> None:
    from voicepipe.commands.triggers import _iter_follow_log
    import threading
    import time

    log = tmp_path / "z.log"
    log.write_text("", encoding="utf-8")

    gen = _iter_follow_log(log, poll_seconds=0.02)

    def rotator():
        # Append a line to the original file, then rotate (rename it away
        # and write a fresh file with new content).
        time.sleep(0.05)
        with open(log, "a", encoding="utf-8") as f:
            f.write("before-rotate\n")
        time.sleep(0.05)
        # Mimic the writer's rotation behavior: move to .1, create new file.
        backup = Path(str(log) + ".1")
        if backup.exists():
            backup.unlink()
        log.rename(backup)
        log.write_text("after-rotate\n", encoding="utf-8")

    t = threading.Thread(target=rotator, daemon=True)
    t.start()

    first = next(gen)
    second = next(gen)
    gen.close()
    assert first == "before-rotate"
    assert second == "after-rotate"


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


# ---------- triggers path ----------


def test_path_prints_canonical_triggers_json_path(runner) -> None:
    from voicepipe.config import triggers_json_path

    result = runner.invoke(main, ["triggers", "path"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == str(triggers_json_path())


# ---------- triggers stats ----------


def _stats_log() -> list[dict]:
    """A small synthetic log covering every verb-extraction path."""
    base = 1779633876900
    return [
        # 3 trigger_match events: 2 zwingli + 1 zwingly
        {"event": "trigger_match", "ts_ms": base, "trigger": "zwingli", "action": "dispatch"},
        {"event": "trigger_match", "ts_ms": base + 1, "trigger": "zwingli", "action": "dispatch"},
        {"event": "trigger_match", "ts_ms": base + 2, "trigger": "zwingly", "action": "dispatch"},
        # 2 dispatch_ok for verb 'python' (meta.verb)
        {
            "event": "dispatch_ok",
            "ts_ms": base + 3,
            "trigger": "zwingli",
            "meta": {"verb": "python", "verb_type": "codegen"},
            "output_text": "ok",
        },
        {
            "event": "dispatch_ok",
            "ts_ms": base + 4,
            "trigger": "zwingli",
            "meta": {"verb": "python", "verb_type": "codegen"},
            "output_text": "ok",
        },
        # 1 dispatch_error for verb 'python'
        {
            "event": "dispatch_error",
            "ts_ms": base + 5,
            "trigger": "zwingli",
            "meta": {"verb": "python"},
            "error": "boom",
        },
        # 1 action_ok for action 'strip'
        {"event": "action_ok", "ts_ms": base + 6, "action": "strip", "output_text": "x"},
        # 1 action_missing for 'nope' — counted as unknown
        {"event": "action_missing", "ts_ms": base + 7, "action": "nope"},
        # 1 rate_limited for 'python'
        {
            "event": "rate_limited",
            "ts_ms": base + 8,
            "verb": "python",
            "cap_per_min": 3,
            "retry_after_seconds": 42,
        },
    ]


def test_stats_text_output_summarizes_triggers_verbs_and_events(
    runner, tmp_path: Path
) -> None:
    log = _write_log(tmp_path / "z.log", _stats_log())
    result = runner.invoke(main, ["triggers", "stats", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "Stats from" in result.output
    assert "9 events" in result.output  # total
    # Triggers section
    assert "zwingli" in result.output
    assert "zwingly" in result.output
    # Verbs section
    assert "python" in result.output
    assert "2 ok" in result.output       # python: 2 ok
    assert "1 err" in result.output      # python: 1 err
    assert "1 limited" in result.output  # python: 1 rate_limited
    assert "1 unknown" in result.output  # nope: 1 action_missing
    # Event types section
    assert "trigger_match" in result.output
    assert "dispatch_ok" in result.output
    assert "rate_limited" in result.output


def test_stats_json_output_carries_all_aggregates(runner, tmp_path: Path) -> None:
    log = _write_log(tmp_path / "z.log", _stats_log())
    result = runner.invoke(main, ["triggers", "stats", "--path", str(log), "--json"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["total_events"] == 9
    assert parsed["trigger_counts"] == {"zwingli": 2, "zwingly": 1}
    assert parsed["event_counts"]["dispatch_ok"] == 2
    assert parsed["event_counts"]["rate_limited"] == 1
    python_summary = parsed["verb_counts"]["python"]
    assert python_summary == {
        "ok": 2,
        "error": 1,
        "rate_limited": 1,
        "unknown": 0,
        "total": 4,
    }
    nope_summary = parsed["verb_counts"]["nope"]
    assert nope_summary["unknown"] == 1
    assert nope_summary["ok"] == 0


def test_stats_top_limits_verb_and_trigger_lists(runner, tmp_path: Path) -> None:
    # 5 distinct verbs, 1 ok each, plus 5 distinct triggers
    events = []
    base = 1779633876900
    for i, verb in enumerate(["v1", "v2", "v3", "v4", "v5"]):
        events.append({"event": "action_ok", "ts_ms": base + i, "action": verb})
    for i, trig in enumerate(["t1", "t2", "t3", "t4", "t5"]):
        events.append(
            {"event": "trigger_match", "ts_ms": base + 10 + i, "trigger": trig}
        )
    log = _write_log(tmp_path / "z.log", events)
    result = runner.invoke(
        main, ["triggers", "stats", "--path", str(log), "--top", "2"]
    )
    assert result.exit_code == 0, result.output
    # Only 2 of each should show — exact ordering depends on insertion order
    # in the Counter for ties, but the count of named verbs in output should
    # be <= 2 for each section.
    verb_hits = sum(1 for v in ["v1", "v2", "v3", "v4", "v5"] if v in result.output)
    trig_hits = sum(1 for t in ["t1", "t2", "t3", "t4", "t5"] if t in result.output)
    assert verb_hits == 2
    assert trig_hits == 2


def test_stats_empty_log_reports_no_events(runner, tmp_path: Path) -> None:
    log = tmp_path / "z.log"
    log.write_text("", encoding="utf-8")
    result = runner.invoke(main, ["triggers", "stats", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "no events" in result.output


def test_stats_missing_file_exits_with_error(runner, tmp_path: Path) -> None:
    result = runner.invoke(
        main, ["triggers", "stats", "--path", str(tmp_path / "missing.log")]
    )
    assert result.exit_code == 1
    assert "✗ debug log not found" in result.output


def test_extract_verb_returns_none_for_unrelated_event_types() -> None:
    from voicepipe.commands.triggers import _extract_verb_from_event

    # Events that intentionally don't carry verb context shouldn't be
    # mis-attributed to a fake verb name.
    assert _extract_verb_from_event({"event": "trigger_match", "trigger": "zwingli"}) is None
    assert _extract_verb_from_event({"event": "shell_start", "command": "ls"}) is None
    assert _extract_verb_from_event({"event": "codegen_complete", "returncode": 0}) is None
    # dispatch_ok without meta.verb is also None (resolution failed before verb)
    assert _extract_verb_from_event({"event": "dispatch_ok", "trigger": "zwingli"}) is None


# ---------- "did you mean?" surfacing in log / dry-run output ----------


def test_log_summary_shows_did_you_mean_for_unknown_verb_dispatch(
    runner, tmp_path: Path
) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "dispatch_ok",
                "ts_ms": 1779633876900,
                "trigger": "zwingli",
                "output_text": "pyhon print",
                "meta": {
                    "mode": "unknown-verb",
                    "verb": "pyhon",
                    "action": "strip",
                    "did_you_mean": ["python"],
                },
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "unknown_verb='pyhon'" in result.output
    assert "did_you_mean=python" in result.output


def test_log_summary_omits_did_you_mean_for_normal_dispatch(
    runner, tmp_path: Path
) -> None:
    log = _write_log(
        tmp_path / "z.log",
        [
            {
                "event": "dispatch_ok",
                "ts_ms": 1779633876900,
                "trigger": "zwingli",
                "output_text": "ok",
                "meta": {"mode": "verb", "verb": "strip"},
            }
        ],
    )
    result = runner.invoke(main, ["triggers", "log", "--path", str(log)])
    assert result.exit_code == 0, result.output
    assert "unknown_verb" not in result.output
    assert "did_you_mean" not in result.output


def test_test_dry_run_output_shows_did_you_mean(runner, tmp_path: Path) -> None:
    cfg = _write(
        tmp_path / "triggers.json",
        {
            "version": 1,
            "triggers": {"zwingli": {"action": "dispatch"}},
            "verbs": {
                "python": {
                    "type": "codegen",
                    "enabled": True,
                    "interpreter": "python3",
                },
                "bash": {
                    "type": "codegen",
                    "enabled": True,
                    "interpreter": "bash",
                },
            },
            "llm_profiles": {},
        },
    )
    result = runner.invoke(
        main, ["triggers", "test", "zwingli pyhon print hello", "--path", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert "resolution: unknown_verb" in result.output
    assert "did_you_mean: python" in result.output
