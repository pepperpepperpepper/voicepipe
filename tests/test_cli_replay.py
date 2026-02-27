from __future__ import annotations

from click.testing import CliRunner

from voicepipe.cli import main
from voicepipe.last_output import clear_last_output, save_last_output


def test_replay_errors_when_empty(isolated_home) -> None:
    clear_last_output()
    runner = CliRunner()
    result = runner.invoke(main, ["replay"])
    assert result.exit_code != 0
    assert "No buffered output found yet" in result.output


def test_replay_outputs_last_text(isolated_home) -> None:
    save_last_output("hi there")
    runner = CliRunner()
    result = runner.invoke(main, ["replay"])
    assert result.exit_code == 0
    assert result.output.strip() == "hi there"


def test_replay_respects_default_clipboard_action(monkeypatch, isolated_home) -> None:
    save_last_output("hi there")
    monkeypatch.setenv("VOICEPIPE_REPLAY_DEFAULT", "clipboard")

    calls: list[str] = []

    import importlib

    replay_cmd = importlib.import_module("voicepipe.commands.replay")

    def _fake_copy(text: str):
        calls.append(text)
        return True, None

    monkeypatch.setattr(replay_cmd, "copy_to_clipboard", _fake_copy)

    runner = CliRunner()
    result = runner.invoke(main, ["replay"])
    assert result.exit_code == 0
    assert result.output.strip() == "hi there"
    assert calls == ["hi there"]


def test_replay_execute_output_escapes_newlines_for_typing(monkeypatch, isolated_home) -> None:
    save_last_output(
        "echo hi",
        payload={
            "transcript_trigger": {
                "meta": {"handler_meta": {"output_preview": "line1\nline2\n"}}
            }
        },
    )

    calls: list[str] = []

    import importlib

    replay_cmd = importlib.import_module("voicepipe.commands.replay")

    def _fake_type(text: str):
        calls.append(text)
        return True, None

    monkeypatch.setattr(replay_cmd, "type_text", _fake_type)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", "--execute-output", "--type"])
    assert result.exit_code == 0
    assert calls == ["line1\\nline2"]


def test_replay_execute_output_raw_keeps_newlines(monkeypatch, isolated_home) -> None:
    save_last_output(
        "echo hi",
        payload={
            "transcript_trigger": {
                "meta": {"handler_meta": {"output_preview": "line1\nline2\n"}}
            }
        },
    )

    calls: list[str] = []

    import importlib

    replay_cmd = importlib.import_module("voicepipe.commands.replay")

    def _fake_type(text: str):
        calls.append(text)
        return True, None

    monkeypatch.setattr(replay_cmd, "type_text", _fake_type)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", "--execute-output", "--type", "--raw"])
    assert result.exit_code == 0
    assert calls == ["line1\nline2"]


def test_replay_types_enter_for_execute_trigger(monkeypatch, isolated_home) -> None:
    save_last_output(
        "ls -la",
        payload={"transcript_trigger": {"meta": {"mode": "verb", "verb_type": "execute"}}},
    )

    calls: list[str] = []

    import importlib

    replay_cmd = importlib.import_module("voicepipe.commands.replay")

    def _fake_type(text: str):
        calls.append(text)
        return True, None

    monkeypatch.setattr(replay_cmd, "type_text", _fake_type)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", "--type"])
    assert result.exit_code == 0
    assert calls == ["ls -la", "\n"]
