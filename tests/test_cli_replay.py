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
