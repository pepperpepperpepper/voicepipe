"""LLM-driven intent router (``action="llm_route"``).

Replaces the lexical ``"VERB ARGS"`` grammar with a model-driven mapping
from raw transcript → list of ``(verb, args)`` steps that the standard
dispatch pipeline then executes. Each step is run through
:func:`_dispatch_single_step` so all existing verb handlers (and their
capability-gated graceful skips, rate limits, audio feedback) apply
unchanged.

Triggers opt in via ``triggers.json``::

    {
      "triggers": {
        "hey": {"action": "llm_route"}
      },
      "llm_profiles": {
        "router": {
          "model": "gpt-4o-mini",
          "temperature": 0.0,
          "system_prompt": "...{verb_registry}..."   // optional
        }
      }
    }

If no ``router`` profile is defined, a built-in default system prompt is
used with the enabled verbs spliced in. The user-defined profile may
include the placeholder ``{verb_registry}`` to receive the same
data-driven verb list.

Single round-trip per dispatch: one LLM call returns a JSON array of
steps. Empty / malformed responses fall back to ``dispatch.unknown_verb``
so the user still gets *something* (usually the raw text typed via
``strip``).
"""

from __future__ import annotations

import json
from typing import Any

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
)

from ._actions import _ACTIONS
from ._actuator import Actuator
from ._debug_log import _write_zwingli_debug_event
from ._llm import _call_llm_with_profile
from ._planner import PlannedStep, execute_plan


ROUTER_PROFILE_NAME = "router"

# Verb-name → short description shown to the LLM. Only verbs that appear
# here AND are enabled in the user's config get exposed to the router —
# unfamiliar verbs would only confuse the model. Extend this map when
# adding new intent verbs.
_VERB_DESCRIPTIONS: dict[str, str] = {
    "alarm": 'set an alarm. args: "<time> [<message>]" e.g. "7:30am wake up", "19:30"',
    "timer": 'start a countdown timer. args: "<duration> [<message>]" e.g. "5 minutes pasta", "1h 30m big bake"',
    "dial": 'place a phone call. args: "<phone number>" — keep `+` and digits, strip words',
    "search": 'web search. args: "<query>"',
    "open": 'open a URL. args: "<url or host>" e.g. "https://example.com"',
    "navigate": 'maps directions. args: "[<mode>] [to] <destination>" mode ∈ {driving, walking, bicycling, transit}',
    "calendar": 'create a calendar event. args: "<event title>" — just the title; omit date/time words (the user sets the time on-device). e.g. "dentist appointment", "team standup"',
    "email": 'compose an email (opens the mail composer; user picks account + sends). args: "to=<recipient or name>; subject=<subject>; body=<message>" — any field may be empty, e.g. "to=bob; subject=lunch; body=are you free at noon"',
    "back": "system Back. no args.",
    "home": "system Home. no args.",
    "recents": "open Recent apps. no args.",
    "notifications": "pull down notification shade. no args.",
    "quick_settings": "pull down quick settings panel. no args.",
    "type": 'type text into focused field. args: "<text>"',
    "strip": 'output text as-is (no action). args: "<text>"',
    "clipboard": 'copy text to clipboard. args: "<text>"',
}


_DEFAULT_SYSTEM_PROMPT = """You are a routing layer for a voice command dispatcher.

Given a transcript, return a JSON array of {"verb": <string>, "args": <string>}
steps that accomplish what the user asked. The dispatcher will execute the
steps in order.

Available verbs (use ONLY these):
{verb_registry}

Rules — follow exactly:
- Output ONLY a JSON array. No markdown fences, no prose, no comments.
- Each item: {"verb": "<name>", "args": "<string>"}.
- Multiple steps are allowed for multi-intent transcripts ("alarm 7am then text mom" → 2 items).
- Clean args to the canonical form the verb expects (e.g. spoken "seven thirty AM" → "7:30am").
- If you cannot confidently map the transcript to any verb, return [].
- If args are not needed, use an empty string.
"""


def _build_verb_registry(commands: TranscriptCommandsConfig) -> str:
    """Render the enabled verbs into the bullet list spliced into the
    router system prompt. Only verbs with a known description are
    included — passing a verb to the LLM without explaining how to call
    it tends to produce hallucinated args."""
    lines: list[str] = []
    for name, cfg in commands.verbs.items():
        if not cfg.enabled:
            continue
        desc = _VERB_DESCRIPTIONS.get(name)
        if not desc:
            continue
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) if lines else "(no verbs enabled)"


def _resolve_router_profile(
    commands: TranscriptCommandsConfig,
) -> TranscriptLLMProfileConfig:
    """Return the LLM profile the router should use.

    Precedence: a user-defined profile under ``ROUTER_PROFILE_NAME`` (if
    present) > the built-in default. In either case, ``{verb_registry}``
    in the system prompt is substituted with the current enabled-verb
    list — so users can customize the surrounding instructions without
    having to maintain the verb catalog by hand.
    """
    user_profile = commands.llm_profiles.get(ROUTER_PROFILE_NAME)
    registry = _build_verb_registry(commands)

    if user_profile is None:
        return TranscriptLLMProfileConfig(
            system_prompt=_DEFAULT_SYSTEM_PROMPT.replace("{verb_registry}", registry),
            temperature=0.0,
        )

    sp = user_profile.system_prompt or _DEFAULT_SYSTEM_PROMPT
    if "{verb_registry}" in sp:
        sp = sp.replace("{verb_registry}", registry)
    return TranscriptLLMProfileConfig(
        model=user_profile.model,
        temperature=user_profile.temperature if user_profile.temperature is not None else 0.0,
        system_prompt=sp,
        user_prompt=user_profile.user_prompt,
        user_prompt_template=user_profile.user_prompt_template,
    )


def _strip_markdown_fences(text: str) -> str:
    """Drop a single pair of ``` fences if the model wrapped its JSON.

    Tolerates the common variants: ```json … ```, ``` … ```, with or
    without a trailing newline. Leaves text alone if no fence is present
    (or only an opening fence — let json.loads error out cleanly).
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence (and optional language tag on the same line).
    if "\n" in stripped:
        first_newline = stripped.index("\n")
        stripped = stripped[first_newline + 1 :]
    else:
        stripped = stripped[3:]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _parse_router_response(text: str) -> list[dict[str, str]]:
    """Parse the LLM response into a list of ``{verb, args}`` dicts.

    Accepts these shapes (in priority order):
    - bare JSON array: ``[{"verb": "alarm", "args": "7am"}]``
    - markdown-fenced JSON array (the model wrapped output in triple
      backticks, optionally tagged with the json language)
    - JSON object with a ``"steps"`` or ``"actions"`` array (defensive
      against models that wrap the array even when told not to)

    Anything else (parse failure, unexpected top-level type, items
    missing the ``verb`` field) becomes ``[]`` — the caller's fallback
    path takes over. Returns trimmed strings; never None.
    """
    cleaned = _strip_markdown_fences(text or "")
    if not cleaned:
        return []
    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    # Some models insist on wrapping arrays in an object — accept it.
    if isinstance(parsed, dict):
        candidate = parsed.get("steps") or parsed.get("actions") or []
        if not isinstance(candidate, list):
            return []
        parsed = candidate
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        verb = item.get("verb")
        if not isinstance(verb, str) or not verb.strip():
            continue
        args = item.get("args", "")
        if not isinstance(args, str):
            args = str(args)
        out.append({"verb": verb.strip(), "args": args.strip()})
    return out


def _plan_llm_route(
    remainder: str,
    *,
    commands: TranscriptCommandsConfig,
) -> tuple[list[PlannedStep], dict[str, Any]]:
    """LLM planner: call the router model, parse its plan into
    :class:`PlannedStep` records. Returns ``(steps, planner_meta)``.

    ``planner_meta`` always carries ``steps`` (the parsed plan,
    inspectable even after execution) and ``router_raw_response`` (for
    debugging hallucinations); it also carries ``llm_meta`` (the API
    response's provider/usage/latency block from
    :func:`_call_llm_with_profile`) when the LLM was actually called.
    Empty remainder short-circuits without an LLM call.
    """
    cleaned = (remainder or "").strip()
    if not cleaned:
        return [], {"steps": [], "reason": "empty-input"}

    profile = _resolve_router_profile(commands)
    llm_text, llm_meta = _call_llm_with_profile(
        cleaned, profile=profile, captures=None
    )
    raw_steps = _parse_router_response(llm_text)

    _write_zwingli_debug_event(
        {
            "event": "llm_route_response",
            "transcript": cleaned,
            "llm_text": llm_text,
            "step_count": len(raw_steps),
        }
    )

    planner_meta: dict[str, Any] = {
        "steps": raw_steps,
        "router_raw_response": llm_text,
        "llm_meta": llm_meta,
    }

    steps: list[PlannedStep] = []
    for raw in raw_steps:
        verb = raw["verb"]
        args = raw["args"]
        # Reconstruct ``"verb args"`` as the raw chunk so pattern-anchored
        # verb matchers in _dispatch_single_step still see the full phrase.
        raw_chunk = f"{verb} {args}".strip() if args else verb
        steps.append(PlannedStep(verb=verb, args=args, raw_chunk=raw_chunk))

    return steps, planner_meta


def _llm_route_prompt(
    remainder: str,
    *,
    commands: TranscriptCommandsConfig,
    actuator: Actuator | None = None,
) -> tuple[str, dict[str, Any]]:
    """LLM-route entry point: plan via the router model, execute via the
    shared planner.

    On an empty-plan response (LLM returned ``[]`` or malformed JSON)
    we fall back to ``dispatch.unknown_verb`` (typically ``strip``) so
    the user still gets *something* — usually the raw text typed via
    the strip handler. The planner_meta records the fallback decision
    so debug logs can tell a routing failure apart from a routing
    success that just happened to fire ``strip``.
    """
    cleaned = (remainder or "").strip()
    steps, planner_meta = _plan_llm_route(cleaned, commands=commands)

    if not steps:
        return _route_fallback(
            cleaned,
            commands=commands,
            actuator=actuator,
            planner_meta=planner_meta,
        )

    return execute_plan(
        steps,
        commands=commands,
        actuator=actuator,
        planner="llm-route",
        planner_meta=planner_meta,
        pipe_prior_output=False,
    )


def _route_fallback(
    cleaned: str,
    *,
    commands: TranscriptCommandsConfig,
    actuator: Actuator | None,
    planner_meta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """When the LLM can't route, run the same fallback path the dispatcher
    uses for unknown verbs (defaults to ``strip``, i.e. type as-is).

    The fallback handler bypasses :func:`_dispatch_single_step` because
    there is no resolved verb — it's the "no plan" path, not a planned
    step. We record ``fallback_action`` in ``planner_meta`` so it's
    introspectable without changing the top-level meta shape.
    """
    fallback_planner_meta = dict(planner_meta)
    unknown_action = (commands.dispatch.unknown_verb or "strip").strip().lower()
    fallback_planner_meta["fallback_action"] = unknown_action

    handler = _ACTIONS.get(unknown_action)
    if handler is None:
        fallback_planner_meta["fallback_error"] = "no_handler"
        return "", {
            "planner": "llm-route",
            "planner_meta": fallback_planner_meta,
        }

    text, inner_meta = handler(
        cleaned,
        verb_cfg=None,
        profiles=commands.llm_profiles,
        captures=None,
        commands=commands,
        actuator=actuator,
    )
    if inner_meta:
        fallback_planner_meta["fallback_meta"] = inner_meta
    return text, {
        "planner": "llm-route",
        "planner_meta": fallback_planner_meta,
    }
