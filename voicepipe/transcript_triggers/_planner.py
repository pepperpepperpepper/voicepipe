"""Shared executor for both the lexical (``dispatch``) and the LLM-driven
(``llm_route``) trigger actions.

Both action types boil down to the same two-phase work:

  1. **Plan**: turn the post-trigger remainder into an ordered list of
     ``(verb, args, raw_chunk)`` steps.
  2. **Execute**: run each step through :func:`_dispatch_single_step` and
     assemble a unified result meta.

This module owns phase 2 + the canonical meta shape, so the two
planners stay narrow (each only has to know how to produce a
``list[PlannedStep]``) and consumers of the result don't have to switch
on which planner produced it.

Result-meta shape (returned by :func:`execute_plan`):

    {
      # Top-level = the LAST step's meta (verb, action, verb_type,
      # handler_meta, destination, etc.), so single-step plans look flat.
      "verb": ...,
      "action": ...,
      ...

      # Only present when N > 1: the prior steps, each step's full meta
      # spread in plus explicit args + output_text keys for inspection.
      "chain": [
        {"verb": ..., "args": ..., "output_text": ..., "action": ..., ...},
        ...
      ],

      # Only present when the LLM planner ran (planner != "lexical"):
      "planner": "llm-route",
      "planner_meta": {"steps": [...], "llm_meta": {...},
                       "router_raw_response": "...", ...},
    }

Empty plans (N=0) return ``("", planner_meta_dict)`` — neither a step
nor a chain to assemble.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voicepipe.config import TranscriptCommandsConfig

from ._actuator import Actuator
from ._dispatch import _dispatch_single_step


@dataclass(frozen=True)
class PlannedStep:
    """A planner's output unit: one verb invocation with its args.

    ``raw_chunk`` is the un-extracted text passed to pattern matchers
    inside :func:`_dispatch_single_step`. For lexical planning it's the
    original chunk before verb extraction (so leading-anchored pattern
    regexes still match). For LLM planning we reconstruct it as
    ``"<verb> <args>"`` since the model only gave us the structured
    fields.
    """

    verb: str
    args: str
    raw_chunk: str


def _build_chain_entry(
    *, verb: str, args: str, output_text: str, step_meta: dict[str, Any]
) -> dict[str, Any]:
    """Wrap one executed step's metadata into a chain[] entry.

    The step's full meta (whatever ``_dispatch_single_step`` produced —
    keys like ``mode``, ``verb``, ``action``, ``handler_meta``,
    ``destination``…) is spread in directly, then we add explicit
    ``args`` + ``output_text`` so a caller iterating the chain can
    reconstruct what each step received and produced without having to
    pair entries against the original plan.
    """
    entry: dict[str, Any] = dict(step_meta)
    entry["args"] = args
    entry["output_text"] = output_text
    return entry


def execute_plan(
    steps: list[PlannedStep],
    *,
    commands: TranscriptCommandsConfig,
    actuator: Actuator | None = None,
    planner: str | None = None,
    planner_meta: dict[str, Any] | None = None,
    pipe_prior_output: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Run a planned step list and return ``(final_text, meta)``.

    Parameters
    ----------
    steps:
        Ordered plan from one of the planners. May be empty — in that
        case we return ``("", planner_meta or {})`` so the caller can
        still inspect why no steps ran.
    commands, actuator:
        Forwarded to each :func:`_dispatch_single_step` call.
    planner:
        Source-of-plan label (currently ``"llm-route"`` for the LLM
        planner; ``None`` / unset for lexical, since that's the default
        path). Added to the top-level meta when set so downstream
        consumers (UI, debug log) can distinguish.
    planner_meta:
        Planner-specific metadata (the LLM call's response, parsed
        plan, fallback markers). Attached to the top-level meta as
        ``planner_meta`` when non-empty.
    pipe_prior_output:
        Lexical chain semantics: a verb-only chain step (e.g. the
        trailing ``copy`` in ``"echo hello then copy"``) takes the prior
        step's output as its input. Set ``True`` for lexical dispatch;
        ``False`` for the LLM planner (the model is expected to emit
        complete args per step).
    """
    if not steps:
        meta: dict[str, Any] = {}
        if planner is not None:
            meta["planner"] = planner
        if planner_meta:
            meta["planner_meta"] = dict(planner_meta)
        return "", meta

    chain_records: list[dict[str, Any]] = []
    prior_output = ""
    last_text = ""
    last_meta: dict[str, Any] = {}

    for i, step in enumerate(steps):
        # First step always uses its own args; subsequent steps may pipe.
        if i == 0:
            step_input = step.args
            step_chunk = step.raw_chunk
        elif step.args.strip() or not pipe_prior_output:
            # Explicit args after the chain verb (lexical) OR LLM planner
            # which is expected to provide complete args: honor what the
            # planner gave us, ignore prior output.
            step_input = step.args
            step_chunk = step.raw_chunk
        else:
            # Verb-only chain step under lexical planning: pipe prior output.
            step_input = prior_output
            step_chunk = prior_output

        step_text, step_meta = _dispatch_single_step(
            step.verb, step_input, step_chunk,
            commands=commands, actuator=actuator,
        )
        prior_output = step_text

        if i < len(steps) - 1:
            chain_records.append(
                _build_chain_entry(
                    verb=step.verb,
                    args=step_input,
                    output_text=step_text,
                    step_meta=step_meta,
                )
            )
        else:
            last_text = step_text
            last_meta = step_meta

    out_meta: dict[str, Any] = dict(last_meta)
    if chain_records:
        out_meta["chain"] = chain_records
    if planner is not None:
        out_meta["planner"] = planner
    if planner_meta:
        out_meta["planner_meta"] = dict(planner_meta)
    return last_text, out_meta
