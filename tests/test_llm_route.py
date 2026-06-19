"""Tests for the LLM-driven intent router (action=llm_route).

Two layers:
  * parser unit tests — exercise :func:`_parse_router_response` directly
    so the model's many "wrap your JSON in markdown" deviations live in
    one place.
  * dispatcher integration — patch :mod:`voicepipe.zwingli`.
    ``process_zwingli_prompt_result`` to return canned router JSON, then
    drive transcripts through :func:`apply_transcript_triggers` and
    assert the actuator received the right calls.
"""
from __future__ import annotations

from typing import Any

import pytest

import voicepipe.config as config
import voicepipe.transcript_triggers as tt
from voicepipe.transcript_triggers._actuator import InMemoryActuator
from voicepipe.transcript_triggers._llm_route import (
    _build_verb_registry,
    _parse_router_response,
    _resolve_router_profile,
    _strip_markdown_fences,
)


# ---------------------------------------------------------------------------
# _strip_markdown_fences
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # No fences — passthrough.
        ("[]", "[]"),
        ('[{"verb":"home"}]', '[{"verb":"home"}]'),
        # Common markdown fence variants.
        ("```\n[]\n```", "[]"),
        ("```json\n[]\n```", "[]"),
        ('```json\n[{"verb":"home","args":""}]\n```', '[{"verb":"home","args":""}]'),
        # Whitespace around fences.
        ("  ```json\n[]\n```  ", "[]"),
        # Trailing newline after closing fence.
        ("```\n[1,2,3]\n```\n", "[1,2,3]"),
        # Inline fence (no newline) is unwrapped too.
        ("```[]```", "[]"),
    ],
)
def test_strip_markdown_fences(raw: str, expected: str) -> None:
    assert _strip_markdown_fences(raw) == expected


# ---------------------------------------------------------------------------
# _parse_router_response
# ---------------------------------------------------------------------------


def test_parse_router_response_bare_array() -> None:
    out = _parse_router_response('[{"verb":"home","args":""}]')
    assert out == [{"verb": "home", "args": ""}]


def test_parse_router_response_multi_step() -> None:
    out = _parse_router_response(
        '[{"verb":"alarm","args":"7am wake up"},'
        '{"verb":"timer","args":"5 minutes pasta"}]'
    )
    assert out == [
        {"verb": "alarm", "args": "7am wake up"},
        {"verb": "timer", "args": "5 minutes pasta"},
    ]


def test_parse_router_response_markdown_fenced() -> None:
    out = _parse_router_response('```json\n[{"verb":"home","args":""}]\n```')
    assert out == [{"verb": "home", "args": ""}]


def test_parse_router_response_object_wrapped_steps_key() -> None:
    """Some models wrap the array in {"steps": [...]} even when told not
    to. Accept both `steps` and `actions` as the wrapping key."""
    out = _parse_router_response('{"steps":[{"verb":"home","args":""}]}')
    assert out == [{"verb": "home", "args": ""}]


def test_parse_router_response_object_wrapped_actions_key() -> None:
    out = _parse_router_response('{"actions":[{"verb":"home","args":""}]}')
    assert out == [{"verb": "home", "args": ""}]


def test_parse_router_response_trims_arg_whitespace() -> None:
    out = _parse_router_response('[{"verb":"alarm","args":"  7am wake up  "}]')
    assert out == [{"verb": "alarm", "args": "7am wake up"}]


def test_parse_router_response_drops_missing_verb() -> None:
    out = _parse_router_response('[{"args":"no verb here"}]')
    assert out == []


def test_parse_router_response_drops_non_string_verb() -> None:
    out = _parse_router_response('[{"verb":42,"args":"x"}]')
    assert out == []


def test_parse_router_response_drops_non_dict_items() -> None:
    out = _parse_router_response('["just a string", {"verb":"home","args":""}]')
    assert out == [{"verb": "home", "args": ""}]


def test_parse_router_response_coerces_non_string_args_to_string() -> None:
    out = _parse_router_response('[{"verb":"timer","args":300}]')
    assert out == [{"verb": "timer", "args": "300"}]


def test_parse_router_response_args_default_to_empty_string() -> None:
    out = _parse_router_response('[{"verb":"home"}]')
    assert out == [{"verb": "home", "args": ""}]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not json at all",
        "{",                           # truncated
        '{"verb":"home"}',             # top-level object without steps key → not a plan
        "42",                           # not a list/object
        '"just a string"',
    ],
)
def test_parse_router_response_garbage_returns_empty(raw: str) -> None:
    assert _parse_router_response(raw) == []


# ---------------------------------------------------------------------------
# _build_verb_registry
# ---------------------------------------------------------------------------


def _commands_with_verbs(verb_names: list[str]) -> config.TranscriptCommandsConfig:
    """Build a commands config with the given verbs enabled. Each verb
    is registered as a builtin pointing to its own name as the action."""
    return config.TranscriptCommandsConfig(
        triggers={"hey": "llm_route"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            name: config.TranscriptVerbConfig(action=name, enabled=True, type="builtin")
            for name in verb_names
        },
    )


def test_build_verb_registry_includes_only_enabled_known_verbs() -> None:
    commands = _commands_with_verbs(["alarm", "timer", "home"])
    registry = _build_verb_registry(commands)
    assert "- alarm:" in registry
    assert "- timer:" in registry
    assert "- home:" in registry


def test_build_verb_registry_skips_unknown_verbs() -> None:
    """Verbs without an entry in _VERB_DESCRIPTIONS are dropped — pushing
    a bare verb name with no usage hint into the LLM only invites
    hallucinated args."""
    commands = _commands_with_verbs(["alarm", "my_custom_verb"])
    registry = _build_verb_registry(commands)
    assert "- alarm:" in registry
    assert "my_custom_verb" not in registry


def test_build_verb_registry_skips_disabled_verbs() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"hey": "llm_route"},
        verbs={
            "alarm": config.TranscriptVerbConfig(action="alarm", enabled=True, type="builtin"),
            "timer": config.TranscriptVerbConfig(action="timer", enabled=False, type="builtin"),
        },
    )
    registry = _build_verb_registry(commands)
    assert "- alarm:" in registry
    assert "- timer:" not in registry


def test_build_verb_registry_handles_empty_verb_set() -> None:
    commands = config.TranscriptCommandsConfig(triggers={"hey": "llm_route"}, verbs={})
    assert _build_verb_registry(commands) == "(no verbs enabled)"


# ---------------------------------------------------------------------------
# _resolve_router_profile
# ---------------------------------------------------------------------------


def test_resolve_router_profile_synthesizes_default_when_user_profile_absent() -> None:
    commands = _commands_with_verbs(["alarm", "home"])
    profile = _resolve_router_profile(commands)
    assert profile.temperature == 0.0
    assert profile.system_prompt is not None
    assert "- alarm:" in profile.system_prompt
    assert "- home:" in profile.system_prompt
    # The placeholder must have been substituted.
    assert "{verb_registry}" not in profile.system_prompt


def test_resolve_router_profile_substitutes_registry_in_user_profile() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"hey": "llm_route"},
        verbs={
            "alarm": config.TranscriptVerbConfig(action="alarm", enabled=True, type="builtin"),
        },
        llm_profiles={
            "router": config.TranscriptLLMProfileConfig(
                model="gpt-test",
                temperature=0.5,
                system_prompt="custom intro\n{verb_registry}\ncustom outro",
            ),
        },
    )
    profile = _resolve_router_profile(commands)
    assert profile.model == "gpt-test"
    assert profile.temperature == 0.5
    assert profile.system_prompt is not None
    assert profile.system_prompt.startswith("custom intro")
    assert profile.system_prompt.endswith("custom outro")
    assert "- alarm:" in profile.system_prompt


def test_resolve_router_profile_keeps_user_system_prompt_without_placeholder() -> None:
    """If the user-defined prompt omits {verb_registry}, leave it alone —
    they've taken full control of how the model is told about verbs."""
    commands = config.TranscriptCommandsConfig(
        triggers={"hey": "llm_route"},
        verbs={"alarm": config.TranscriptVerbConfig(action="alarm", enabled=True, type="builtin")},
        llm_profiles={
            "router": config.TranscriptLLMProfileConfig(
                system_prompt="custom prompt, no placeholder",
            ),
        },
    )
    profile = _resolve_router_profile(commands)
    assert profile.system_prompt == "custom prompt, no placeholder"


# ---------------------------------------------------------------------------
# Integration via apply_transcript_triggers (with mocked LLM)
# ---------------------------------------------------------------------------


def _make_router_commands() -> config.TranscriptCommandsConfig:
    """Standard fixture: hey trigger → llm_route, plus a representative
    set of verbs the router can pick from."""
    return config.TranscriptCommandsConfig(
        triggers={"hey": "llm_route"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "alarm": config.TranscriptVerbConfig(action="alarm", enabled=True, type="builtin"),
            "timer": config.TranscriptVerbConfig(action="timer", enabled=True, type="builtin"),
            "search": config.TranscriptVerbConfig(action="search", enabled=True, type="builtin"),
            "open": config.TranscriptVerbConfig(action="open", enabled=True, type="builtin"),
            "navigate": config.TranscriptVerbConfig(action="navigate", enabled=True, type="builtin"),
            "home": config.TranscriptVerbConfig(action="home", enabled=True, type="builtin"),
            "back": config.TranscriptVerbConfig(action="back", enabled=True, type="builtin"),
        },
    )


def _patch_router_response(monkeypatch, llm_response: str) -> dict[str, Any]:
    """Patch the LLM call site to return `llm_response`. Returns a dict
    that will be populated with the captured (prompt, kwargs) the
    handler sent — useful for asserting the model got the right input."""
    captured: dict[str, Any] = {}

    def _fake_process(prompt: str, **kwargs) -> tuple[str, dict]:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return llm_response, {"provider": "fake", "model": kwargs.get("model")}

    import voicepipe.zwingli as zwingli
    monkeypatch.setattr(zwingli, "process_zwingli_prompt_result", _fake_process)
    return captured


def test_llm_route_happy_path_single_step(monkeypatch) -> None:
    """Router returns one step → that verb fires through the normal pipeline."""
    captured = _patch_router_response(
        monkeypatch, '[{"verb":"home","args":""}]'
    )
    act = InMemoryActuator()
    out, payload = tt.apply_transcript_triggers(
        "hey go home",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert out == ""
    assert payload is not None
    assert payload["ok"] is True
    assert payload["action"] == "llm_route"
    # Under the unified planner shape, the planner source goes in `planner`
    # and the parsed plan lives under `planner_meta.steps`. The top-level
    # `mode` reflects the LAST step's verb-resolution mode (here: "verb"
    # because the home verb resolved cleanly).
    assert payload["meta"]["planner"] == "llm-route"
    assert payload["meta"]["planner_meta"]["steps"] == [
        {"verb": "home", "args": ""}
    ]
    assert payload["meta"]["mode"] == "verb"  # last step's resolution mode
    # The home verb fired through the standard pipeline.
    assert act.accessibility_global_calls == ["home"]
    # The router got the post-trigger remainder, not the raw transcript.
    assert captured["prompt"] == "go home"
    # The default router profile uses temperature 0.0.
    assert captured["kwargs"]["temperature"] == 0.0
    # Single-step plans don't get a `chain` key — top-level IS the only step.
    assert "chain" not in payload["meta"]


def test_llm_route_multi_step(monkeypatch) -> None:
    """One transcript → multiple parsed steps → both verbs fire in order."""
    _patch_router_response(
        monkeypatch,
        '[{"verb":"alarm","args":"7am wake up"},'
        '{"verb":"timer","args":"5 minutes pasta"}]',
    )
    act = InMemoryActuator()
    out, payload = tt.apply_transcript_triggers(
        "hey set alarm for seven AM wake up and a five minute pasta timer",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload is not None
    assert payload["ok"] is True
    assert act.set_alarm_calls == [
        {"hour": 7, "minutes": 0, "message": "wake up", "in_seconds": None}
    ]
    assert act.set_timer_calls == [{"seconds": 300, "message": "pasta"}]
    # Multi-step: prior step(s) live in `chain`; the last step's meta is
    # promoted to the top level.
    assert payload["meta"]["verb"] == "timer"  # last step
    chain = payload["meta"]["chain"]
    assert len(chain) == 1  # alarm was the only prior step
    assert chain[0]["verb"] == "alarm"
    # New under the unified shape: chain entries carry the resolved args and
    # the step's output text alongside the step's own meta keys.
    assert chain[0]["args"] == "7am wake up"
    assert chain[0]["output_text"] == ""  # alarm returns empty text
    # The full parsed plan is also retained under planner_meta for debug.
    assert len(payload["meta"]["planner_meta"]["steps"]) == 2


def test_llm_route_markdown_fenced_response_still_routes(monkeypatch) -> None:
    """Fenced JSON from a chatty model still gets parsed."""
    _patch_router_response(
        monkeypatch, '```json\n[{"verb":"home","args":""}]\n```'
    )
    act = InMemoryActuator()
    _, payload = tt.apply_transcript_triggers(
        "hey go to the home screen",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload is not None
    assert payload["ok"] is True
    assert act.accessibility_global_calls == ["home"]


def test_llm_route_empty_response_falls_back_to_unknown_verb(monkeypatch) -> None:
    """LLM returns [] → fallback to dispatch.unknown_verb (strip),
    so the user still gets the text typed instead of dropping silently."""
    _patch_router_response(monkeypatch, "[]")
    act = InMemoryActuator()
    out, payload = tt.apply_transcript_triggers(
        "hey something the model cannot map",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload is not None
    assert payload["ok"] is True
    # Strip just echoes the cleaned input.
    assert out == "something the model cannot map"
    # The fallback decision lives under planner_meta so the top-level shape
    # remains the strip handler's (empty) meta.
    assert payload["meta"]["planner"] == "llm-route"
    assert payload["meta"]["planner_meta"]["fallback_action"] == "strip"
    assert payload["meta"]["planner_meta"]["steps"] == []
    # No verb-side actuator calls.
    assert act.set_alarm_calls == []
    assert act.accessibility_global_calls == []


def test_llm_route_malformed_response_falls_back(monkeypatch) -> None:
    """Same as the empty case — unparseable JSON should still let the
    user get their text rather than hitting a hard error."""
    _patch_router_response(monkeypatch, "this is not JSON, sorry")
    act = InMemoryActuator()
    out, payload = tt.apply_transcript_triggers(
        "hey nonsense",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload["ok"] is True
    assert payload["meta"]["planner_meta"]["fallback_action"] == "strip"
    assert out == "nonsense"


def test_llm_route_hallucinated_verb_runs_unknown_verb_step(monkeypatch) -> None:
    """If the LLM picks a verb not in the registry, the dispatcher's
    per-step unknown-verb fallback handles it — the rest of the chain
    isn't aborted."""
    _patch_router_response(
        monkeypatch,
        '[{"verb":"summon_dragon","args":"big red one"},'
        '{"verb":"home","args":""}]',
    )
    act = InMemoryActuator()
    _, payload = tt.apply_transcript_triggers(
        "hey two-step request",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload["ok"] is True
    # First step's hallucinated verb fell through to unknown-verb fallback
    # and lives in `chain[0]` under the unified shape. The verb name is
    # still recorded so debug logs can show what the LLM hallucinated.
    chain = payload["meta"]["chain"]
    assert chain[0]["verb"] == "summon_dragon"
    assert chain[0]["mode"] == "unknown-verb"
    assert chain[0]["args"] == "big red one"
    # Second step (home) ran normally and is the promoted top-level meta.
    assert payload["meta"]["verb"] == "home"
    assert act.accessibility_global_calls == ["home"]


def test_llm_route_empty_remainder_short_circuits(monkeypatch) -> None:
    """No transcript after the trigger → don't burn an LLM call."""
    captured = _patch_router_response(monkeypatch, "[]")
    act = InMemoryActuator()
    out, payload = tt.apply_transcript_triggers(
        "hey", commands=_make_router_commands(), actuator=act,
    )
    # Trigger matched "hey" but remainder is empty.
    assert payload is not None
    assert payload["meta"]["planner"] == "llm-route"
    assert payload["meta"]["planner_meta"]["steps"] == []
    assert payload["meta"]["planner_meta"]["reason"] == "empty-input"
    # The LLM was never invoked.
    assert "prompt" not in captured


def test_llm_route_step_with_capability_unsupported_graceful_skips(monkeypatch) -> None:
    """LLM picks a verb whose capability the client lacks → the step's
    handler returns the standard graceful-skip warning. The router
    surfaces it cleanly without aborting other steps."""
    from voicepipe.transcript_triggers._actuator import CAP_OPEN_URL

    _patch_router_response(
        monkeypatch,
        '[{"verb":"alarm","args":"7am"},{"verb":"home","args":""}]',
    )
    # Actuator lacks alarm AND accessibility_global caps.
    bare = frozenset({CAP_OPEN_URL})
    act = InMemoryActuator(caps=bare)
    _, payload = tt.apply_transcript_triggers(
        "hey two unsupported things",
        commands=_make_router_commands(),
        actuator=act,
    )
    assert payload["ok"] is True
    # Both steps ran and both surfaced graceful-skip in their handler_meta.
    # alarm is the prior step (in `chain`), home is the last step (top-level).
    chain = payload["meta"]["chain"]
    assert chain[0]["verb"] == "alarm"
    assert chain[0]["handler_meta"]["ok"] is False
    assert payload["meta"]["verb"] == "home"
    assert payload["meta"]["handler_meta"]["ok"] is False
    assert act.set_alarm_calls == []
    assert act.accessibility_global_calls == []


def test_llm_route_handler_exception_propagates_to_error_payload(monkeypatch) -> None:
    """If the LLM call itself raises (network error, key missing, etc),
    the standard error-handling path in apply_transcript_triggers takes
    over and produces an `ok=false` payload — same shape as dispatch."""
    def _explode(prompt: str, **kwargs):
        raise RuntimeError("LLM API unreachable")

    import voicepipe.zwingli as zwingli
    monkeypatch.setattr(zwingli, "process_zwingli_prompt_result", _explode)

    out, payload = tt.apply_transcript_triggers(
        "hey set alarm",
        commands=_make_router_commands(),
        actuator=InMemoryActuator(),
    )
    assert payload is not None
    assert payload["ok"] is False
    assert "LLM API unreachable" in payload["error"]
