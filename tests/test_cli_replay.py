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

