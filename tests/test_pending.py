from __future__ import annotations

import time
from pathlib import Path

import pytest

import voicepipe.pending as pending_mod


@pytest.fixture
def pending_in_tmp(tmp_path: Path, monkeypatch) -> Path:
    """Redirect pending storage to a temporary file."""
    path = tmp_path / "pending-command.json"

    def _fake_path(*, create_dir: bool = False) -> Path:
        if create_dir:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(pending_mod, "pending_path", _fake_path)
    return path


def test_save_and_load_pending_roundtrips(pending_in_tmp: Path) -> None:
    entry = pending_mod.make_pending(
        verb="subprocess", verb_type="shell", command="ls -la", timeout_seconds=30
    )
    pending_mod.save_pending(entry)

    loaded = pending_mod.load_pending()
    assert loaded is not None
    assert loaded.verb == "subprocess"
    assert loaded.verb_type == "shell"
    assert loaded.command == "ls -la"
    assert loaded.expires_at > loaded.created_at


def test_load_pending_missing_returns_none(pending_in_tmp: Path) -> None:
    assert pending_mod.load_pending() is None


def test_load_pending_corrupt_clears_and_returns_none(pending_in_tmp: Path) -> None:
    pending_in_tmp.parent.mkdir(parents=True, exist_ok=True)
    pending_in_tmp.write_text("not json at all", encoding="utf-8")
    assert pending_mod.load_pending() is None
    assert not pending_in_tmp.exists()


def test_clear_pending_removes_file(pending_in_tmp: Path) -> None:
    entry = pending_mod.make_pending(
        verb="subprocess", verb_type="shell", command="ls", timeout_seconds=30
    )
    pending_mod.save_pending(entry)
    assert pending_in_tmp.exists()
    pending_mod.clear_pending()
    assert not pending_in_tmp.exists()


def test_load_pending_returns_none_when_expired(pending_in_tmp: Path) -> None:
    now = time.time()
    entry = pending_mod.make_pending(
        verb="subprocess",
        verb_type="shell",
        command="ls",
        timeout_seconds=5,
        now=now - 10,  # 10s ago, with 5s timeout -> already expired
    )
    pending_mod.save_pending(entry)
    assert pending_mod.load_pending(now=now) is None
    assert not pending_in_tmp.exists()


def test_save_pending_overwrites_prior(pending_in_tmp: Path) -> None:
    pending_mod.save_pending(
        pending_mod.make_pending(verb="x", verb_type="shell", command="ls", timeout_seconds=30)
    )
    pending_mod.save_pending(
        pending_mod.make_pending(verb="y", verb_type="execute", command="echo hi", timeout_seconds=30)
    )
    loaded = pending_mod.load_pending()
    assert loaded is not None
    assert loaded.verb == "y"
    assert loaded.verb_type == "execute"
    assert loaded.command == "echo hi"


def test_pending_with_interpreter_roundtrips(pending_in_tmp: Path) -> None:
    entry = pending_mod.make_pending(
        verb="pyrun",
        verb_type="script",
        command="print('hi')",
        timeout_seconds=30,
        interpreter="python3",
    )
    pending_mod.save_pending(entry)
    loaded = pending_mod.load_pending()
    assert loaded is not None
    assert loaded.verb_type == "script"
    assert loaded.interpreter == "python3"
    assert loaded.command == "print('hi')"
