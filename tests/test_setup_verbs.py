"""Tests for ``_ensure_zwingli_verbs_in_triggers_json``.

The setup-time helper that idempotently writes Zwingli's expected verbs
into ``triggers.json``. Adding the intent-style verbs (search/open/alarm/
timer/dial) made this code do enough that it deserves dedicated coverage.
"""
from __future__ import annotations

import json
from pathlib import Path

from voicepipe.commands.setup import (
    _DEFAULT_INTENT_VERBS,
    _ensure_builtin_verb,
    _ensure_zwingli_verbs_in_triggers_json,
)


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# _ensure_builtin_verb unit behaviour
# ---------------------------------------------------------------------------


def test_ensure_builtin_verb_adds_missing_entry() -> None:
    verbs: dict = {}
    assert _ensure_builtin_verb(verbs, "search") is True
    assert verbs == {"search": {"type": "builtin", "enabled": True}}


def test_ensure_builtin_verb_repairs_wrong_type() -> None:
    verbs = {"alarm": {"type": "shell", "enabled": True}}
    assert _ensure_builtin_verb(verbs, "alarm") is True
    assert verbs["alarm"]["type"] == "builtin"
    assert verbs["alarm"]["enabled"] is True


def test_ensure_builtin_verb_enables_when_disabled() -> None:
    verbs = {"timer": {"type": "builtin", "enabled": False}}
    assert _ensure_builtin_verb(verbs, "timer") is True
    assert verbs["timer"]["enabled"] is True


def test_ensure_builtin_verb_idempotent_when_already_correct() -> None:
    verbs = {"dial": {"type": "builtin", "enabled": True}}
    assert _ensure_builtin_verb(verbs, "dial") is False
    assert verbs == {"dial": {"type": "builtin", "enabled": True}}


def test_ensure_builtin_verb_preserves_extra_keys() -> None:
    """Custom keys a user has added (aliases, pattern, etc.) must survive."""
    verbs = {
        "search": {
            "type": "builtin",
            "enabled": True,
            "aliases": ["look up", "lookup"],
        }
    }
    assert _ensure_builtin_verb(verbs, "search") is False
    assert verbs["search"]["aliases"] == ["look up", "lookup"]


# ---------------------------------------------------------------------------
# _ensure_zwingli_verbs_in_triggers_json — end-to-end on a file
# ---------------------------------------------------------------------------


def test_setup_writes_all_default_intent_verbs(tmp_path: Path) -> None:
    triggers = tmp_path / "triggers.json"
    _write(triggers, {"verbs": {}})

    assert _ensure_zwingli_verbs_in_triggers_json(triggers) is True

    payload = _read(triggers)
    verbs = payload["verbs"]
    # Every default intent verb is present as enabled builtin.
    for name in _DEFAULT_INTENT_VERBS:
        assert verbs[name]["type"] == "builtin", f"{name!r} missing or wrong type"
        assert verbs[name]["enabled"] is True, f"{name!r} should be enabled"
    # The original three Zwingli verbs are still there.
    assert verbs["execute"]["enabled"] is True
    assert verbs["subprocess"]["enabled"] is True
    assert verbs["type"]["enabled"] is True


def test_setup_default_intent_verbs_list_matches_intents_module() -> None:
    """If the setup default list drifts away from what _intents.py
    registers, fresh installs would silently miss verbs. Pin the contract."""
    from voicepipe.transcript_triggers._actions import _ACTIONS

    for name in _DEFAULT_INTENT_VERBS:
        assert name in _ACTIONS, (
            f"setup advertises verb {name!r} but no action handler is registered"
        )


def test_setup_idempotent_when_intent_verbs_already_present(tmp_path: Path) -> None:
    triggers = tmp_path / "triggers.json"
    _write(
        triggers,
        {
            "verbs": {
                "execute": {"type": "execute", "enabled": True, "timeout_seconds": 10},
                "subprocess": {
                    "type": "shell",
                    "enabled": True,
                    "timeout_seconds": 10,
                },
                "type": {"type": "type", "enabled": True},
                "search": {"type": "builtin", "enabled": True},
                "open": {"type": "builtin", "enabled": True},
                "alarm": {"type": "builtin", "enabled": True},
                "timer": {"type": "builtin", "enabled": True},
                "dial": {"type": "builtin", "enabled": True},
                "navigate": {"type": "builtin", "enabled": True},
                "back": {"type": "builtin", "enabled": True},
                "home": {"type": "builtin", "enabled": True},
                "recents": {"type": "builtin", "enabled": True},
                "notifications": {"type": "builtin", "enabled": True},
                "quick_settings": {"type": "builtin", "enabled": True},
            }
        },
    )
    # Capture mtime; if nothing changes, the file shouldn't be rewritten.
    before = triggers.read_bytes()

    assert _ensure_zwingli_verbs_in_triggers_json(triggers) is False
    assert triggers.read_bytes() == before


def test_setup_preserves_user_disabled_other_verbs(tmp_path: Path) -> None:
    """If a user has already turned a non-Zwingli verb off, we must not
    re-enable it as a side effect."""
    triggers = tmp_path / "triggers.json"
    _write(
        triggers,
        {
            "verbs": {
                "my_custom_verb": {"type": "builtin", "enabled": False},
            }
        },
    )
    _ensure_zwingli_verbs_in_triggers_json(triggers)
    verbs = _read(triggers)["verbs"]
    assert verbs["my_custom_verb"]["enabled"] is False


def test_setup_repairs_wrong_type_for_intent_verbs(tmp_path: Path) -> None:
    """If a user mistyped a verb's type, setup should fix it (matches the
    existing repair behavior for execute/subprocess/type)."""
    triggers = tmp_path / "triggers.json"
    _write(
        triggers,
        {
            "verbs": {
                "alarm": {"type": "shell", "enabled": True},
            }
        },
    )
    assert _ensure_zwingli_verbs_in_triggers_json(triggers) is True
    verbs = _read(triggers)["verbs"]
    assert verbs["alarm"]["type"] == "builtin"


def test_setup_resulting_triggers_json_parses_back_to_working_verbs(
    tmp_path: Path,
) -> None:
    """End-to-end: after setup, loading the file via the config parser
    yields verbs with action names that resolve in _ACTIONS, so a
    dispatch call would actually find a handler."""
    import voicepipe.config as config
    from voicepipe.transcript_triggers._actions import _ACTIONS

    triggers = tmp_path / "triggers.json"
    _write(triggers, {"verbs": {}})
    _ensure_zwingli_verbs_in_triggers_json(triggers)

    raw = _read(triggers)
    parsed_verbs = config._parse_transcript_verbs_json_obj(raw)

    for name in _DEFAULT_INTENT_VERBS:
        assert name in parsed_verbs, f"{name!r} missing after parse"
        verb = parsed_verbs[name]
        assert verb.enabled is True
        # action must resolve to a real handler
        assert verb.action in _ACTIONS, (
            f"verb {name!r} resolved to action {verb.action!r} which has no handler"
        )
