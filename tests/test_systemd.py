from __future__ import annotations

from pathlib import Path

from voicepipe.systemd import (
    RECORDER_UNIT,
    TARGET_UNIT,
    TRANSCRIBER_UNIT,
    install_user_units,
    render_recorder_unit,
    render_target_unit,
    render_transcriber_unit,
)


def test_render_recorder_unit_includes_target_and_env_file() -> None:
    unit = render_recorder_unit()
    assert f"PartOf={TARGET_UNIT}" in unit
    assert "EnvironmentFile=-%h/.config/voicepipe/voicepipe.env" in unit


def test_render_transcriber_unit_includes_target_and_env_file() -> None:
    unit = render_transcriber_unit()
    assert f"PartOf={TARGET_UNIT}" in unit
    assert "EnvironmentFile=-%h/.config/voicepipe/voicepipe.env" in unit


def test_render_target_unit_wants_both_services() -> None:
    unit = render_target_unit()
    assert f"Wants={RECORDER_UNIT} {TRANSCRIBER_UNIT}" in unit


def test_install_user_units_writes_three_units(tmp_path: Path) -> None:
    result = install_user_units(unit_dir=tmp_path)
    assert result.recorder_path == tmp_path / RECORDER_UNIT
    assert result.transcriber_path == tmp_path / TRANSCRIBER_UNIT
    assert result.target_path == tmp_path / TARGET_UNIT
    assert "Voicepipe Recording Service" in result.recorder_path.read_text(encoding="utf-8")
    assert "Voicepipe Transcriber Service" in result.transcriber_path.read_text(
        encoding="utf-8"
    )
    assert "Voicepipe (Recorder + Transcriber)" in result.target_path.read_text(
        encoding="utf-8"
    )
