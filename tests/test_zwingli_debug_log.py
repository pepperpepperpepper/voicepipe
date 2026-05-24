from __future__ import annotations

from pathlib import Path

import pytest

import voicepipe.transcript_triggers as tt


# --- env-var parsing ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12345", 12345),
        ("4K", 4 * 1024),
        ("4k", 4 * 1024),
        ("10M", 10 * 1024 * 1024),
        ("10m", 10 * 1024 * 1024),
        ("2G", 2 * 1024 * 1024 * 1024),
        ("1.5M", int(1.5 * 1024 * 1024)),
        (" 8K ", 8 * 1024),
    ],
)
def test_max_bytes_parses_suffix_forms(monkeypatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", raw)
    assert tt._zwingli_debug_log_max_bytes() == expected


def test_max_bytes_defaults_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", raising=False)
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


def test_max_bytes_empty_string_defaults(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "   ")
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


def test_max_bytes_zero_means_disabled(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "0")
    assert tt._zwingli_debug_log_max_bytes() == 0


def test_max_bytes_invalid_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "not-a-number")
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


def test_max_bytes_invalid_suffix_falls_back_to_default(monkeypatch) -> None:
    # Unknown suffix "T" is not stripped, so float("10T") fails parse.
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "10T")
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


def test_max_bytes_negative_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "-100")
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


def test_max_bytes_negative_with_suffix_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "-1M")
    assert tt._zwingli_debug_log_max_bytes() == tt._ZWINGLI_DEBUG_LOG_DEFAULT_MAX_BYTES


# --- rotation behavior ---


def test_rotate_skipped_when_disabled_via_zero(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "0")
    log = tmp_path / "zwingli-debug.log"
    log.write_bytes(b"x" * 4096)
    tt._maybe_rotate_debug_log(log)
    assert log.exists()
    assert log.read_bytes() == b"x" * 4096
    assert not (tmp_path / "zwingli-debug.log.1").exists()


def test_rotate_skipped_when_size_below_threshold(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "1K")
    log = tmp_path / "zwingli-debug.log"
    log.write_bytes(b"x" * 500)
    tt._maybe_rotate_debug_log(log)
    assert log.exists()
    assert not (tmp_path / "zwingli-debug.log.1").exists()


def test_rotate_moves_log_to_backup_when_threshold_exceeded(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "1K")
    log = tmp_path / "zwingli-debug.log"
    log.write_bytes(b"x" * 2048)
    tt._maybe_rotate_debug_log(log)
    assert not log.exists()
    backup = tmp_path / "zwingli-debug.log.1"
    assert backup.exists()
    assert backup.read_bytes() == b"x" * 2048


def test_rotate_overwrites_existing_backup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "1K")
    log = tmp_path / "zwingli-debug.log"
    backup = tmp_path / "zwingli-debug.log.1"
    backup.write_bytes(b"stale-rotation")
    log.write_bytes(b"x" * 2048)
    tt._maybe_rotate_debug_log(log)
    assert not log.exists()
    assert backup.read_bytes() == b"x" * 2048


def test_rotate_handles_missing_log_gracefully(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VOICEPIPE_ZWINGLI_DEBUG_LOG_MAX_BYTES", "1K")
    log = tmp_path / "no-such-log"
    tt._maybe_rotate_debug_log(log)
    assert not log.exists()
