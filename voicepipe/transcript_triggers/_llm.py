"""LLM-backed verb (``action=zwingli``) plus the shared LLM call helper.

``_call_llm_with_profile`` is split out because the codegen handler reuses
it (LLM generates a script, then a different subsystem runs it).
"""

from __future__ import annotations

from typing import Any, Mapping

from voicepipe.config import (
    TranscriptCommandsConfig,
    TranscriptLLMProfileConfig,
    TranscriptVerbConfig,
)

from ._template import _render_user_prompt_template


def _call_llm_with_profile(
    prompt: str,
    *,
    profile: TranscriptLLMProfileConfig | None,
    captures: Mapping[str, str] | None,
) -> tuple[str, dict[str, Any]]:
    """Render the profile's user-prompt template against `prompt` and call the LLM.

    Returns (output_text, meta). `meta["template_applied"]` is set when the
    profile's user_prompt_template was used.
    """
    from voicepipe.zwingli import process_zwingli_prompt_result

    rendered_prompt = prompt
    template_applied = False
    if profile is not None and profile.user_prompt_template:
        rendered_prompt = _render_user_prompt_template(
            profile.user_prompt_template, text=prompt, captures=captures
        )
        template_applied = True

    if profile is not None:
        text, meta = process_zwingli_prompt_result(
            rendered_prompt,
            model=profile.model,
            temperature=profile.temperature,
            system_prompt=profile.system_prompt,
            user_prompt=profile.user_prompt,
        )
    else:
        text, meta = process_zwingli_prompt_result(rendered_prompt)

    if not isinstance(meta, dict):
        meta = {"meta": meta}
    else:
        meta = dict(meta)
    if template_applied:
        meta["template_applied"] = True
    return text, meta


def _action_zwingli(
    prompt: str,
    *,
    verb_cfg: TranscriptVerbConfig | None = None,
    profiles: Mapping[str, TranscriptLLMProfileConfig] | None = None,
    captures: Mapping[str, str] | None = None,
    commands: TranscriptCommandsConfig | None = None,
) -> tuple[str, dict[str, Any]]:
    del commands

    profile_name = ""
    if verb_cfg is not None:
        profile_name = (getattr(verb_cfg, "profile", "") or "").strip().lower()

    profile: TranscriptLLMProfileConfig | None = None
    if profile_name and profiles is not None:
        profile = profiles.get(profile_name)

    text, meta = _call_llm_with_profile(prompt, profile=profile, captures=captures)
    if profile_name:
        meta["profile_found"] = profile is not None
    return text, meta
