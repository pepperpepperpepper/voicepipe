from __future__ import annotations

import subprocess

import pytest

import voicepipe.config as config
import voicepipe.transcript_triggers as tt


def test_match_transcript_trigger_prefix_variants() -> None:
    triggers = {"zwingly": "zwingli"}

    m = tt.match_transcript_trigger("zwingly do it", triggers=triggers)
    assert m is not None
    assert m.trigger == "zwingly"
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("Zwingly, do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly: do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly; do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly. do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"


def test_match_transcript_trigger_allows_whitespace_before_separators() -> None:
    triggers = {"zwingly": "zwingli"}

    m = tt.match_transcript_trigger("Zwingly , do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly : do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly ; do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly . do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"


def test_match_transcript_trigger_allows_separator_words() -> None:
    triggers = {"zwingly": "zwingli"}

    m = tt.match_transcript_trigger("zwingly comma do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly colon do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly semicolon do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"

    m = tt.match_transcript_trigger("zwingly period do it", triggers=triggers)
    assert m is not None
    assert m.remainder == "do it"


def test_match_transcript_trigger_requires_boundary() -> None:
    triggers = {"zwingly": "zwingli"}
    assert tt.match_transcript_trigger("zwinglyx do it", triggers=triggers) is None


def test_apply_transcript_triggers_no_match_returns_original() -> None:
    out, meta = tt.apply_transcript_triggers("hello world", triggers={"zwingly": "zwingli"})
    assert out == "hello world"
    assert meta is None


def test_apply_transcript_triggers_invokes_handler(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_handler(prompt: str):
        calls.append(prompt)
        return "processed", {"provider": "fake"}

    monkeypatch.setitem(tt._ACTIONS, "zwingli", _fake_handler)

    out, meta = tt.apply_transcript_triggers("zwingly do it", triggers={"zwingly": "zwingli"})
    assert calls == ["do it"]
    assert out == "processed"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["trigger"] == "zwingly"
    assert meta["action"] == "zwingli"
    assert meta["meta"] == {"provider": "fake"}


def test_apply_transcript_triggers_unknown_action_falls_back() -> None:
    out, meta = tt.apply_transcript_triggers("zwingly do it", triggers={"zwingly": "nope"})
    assert out.startswith("⚠ zwingli: ")
    assert "Unknown transcript trigger action" in out
    assert meta is not None
    assert meta["ok"] is False
    assert meta["error"]
    assert meta["meta"]["error_destination"] == "type"


def test_apply_transcript_triggers_shell_disabled(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)
    out, meta = tt.apply_transcript_triggers("zwingli echo hi", triggers={"zwingli": "shell"})
    assert out.startswith("⚠ zwingli: ")
    assert "VOICEPIPE_SHELL_ALLOW" in out
    assert meta is not None
    assert meta["ok"] is False
    assert "VOICEPIPE_SHELL_ALLOW" in meta["error"]
    assert meta["meta"]["error_destination"] == "type"


def test_apply_transcript_triggers_shell_executes(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    def _fake_run(cmd, **kwargs):
        assert cmd == "echo hi"
        assert kwargs.get("shell") is True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    out, meta = tt.apply_transcript_triggers("zwingli echo hi", triggers={"zwingli": "shell"})
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["returncode"] == 0


def test_apply_transcript_triggers_shell_strips_trailing_sentence_punct(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    def _fake_run(cmd, **kwargs):
        assert cmd == "echo hi"
        assert kwargs.get("shell") is True
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    out, meta = tt.apply_transcript_triggers("zwingli echo hi.", triggers={"zwingli": "shell"})
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True


def test_apply_transcript_triggers_dispatch_unknown_verb_strips_remainder() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={},
    )
    out, meta = tt.apply_transcript_triggers("zwingli ps aux", commands=commands)
    assert out == "ps aux"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "unknown-verb"
    assert meta["meta"]["verb"] == "ps"


def test_apply_transcript_triggers_dispatch_known_verb_routes() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli strip hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "strip"
    assert meta["meta"]["action"] == "strip"


def test_apply_transcript_triggers_dispatch_includes_destination_metadata() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                destination="clipboard",
            ),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli strip hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["destination"] == "clipboard"


def test_apply_transcript_triggers_dispatch_shell_uses_verb_timeout_seconds(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")
    monkeypatch.setenv("VOICEPIPE_SHELL_TIMEOUT_SECONDS", "99")

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "subprocess": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                timeout_seconds=5.0,
            )
        },
    )

    def _fake_run(cmd, **kwargs):
        assert cmd == "echo hi"
        assert kwargs.get("timeout") == 5.0
        assert kwargs.get("shell") is True
        assert kwargs.get("capture_output") is True
        assert kwargs.get("stdin") is subprocess.DEVNULL
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="hello\n", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    out, meta = tt.apply_transcript_triggers("zwingli subprocess echo hi", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "subprocess"
    assert meta["meta"]["action"] == "shell"
    assert meta["meta"]["timeout_seconds"] == 5.0
    assert meta["meta"]["handler_meta"]["timeout_seconds"] == 5.0


def test_apply_transcript_triggers_dispatch_execute_types_command_and_requests_enter(monkeypatch) -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
                action="execute",
                enabled=True,
                type="execute",
                timeout_seconds=5.0,
            )
        },
    )

    def _fake_run(*_args, **_kwargs):
        raise AssertionError("execute must not spawn a subprocess")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    out, meta = tt.apply_transcript_triggers("zwingli execute echo hi.", commands=commands)
    assert out == "echo hi"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "execute"
    assert meta["meta"]["verb_type"] == "execute"
    assert meta["meta"]["action"] == "execute"
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["enter"] is True


def test_apply_transcript_triggers_dispatch_type_parses_key_sequence() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "type": config.TranscriptVerbConfig(
                action="type",
                enabled=True,
                type="type",
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli type up arrow up arrow up", commands=commands)
    assert out == "up up up"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "type"
    assert meta["meta"]["verb_type"] == "type"
    assert meta["meta"]["action"] == "type"
    handler_meta = meta["meta"]["handler_meta"]
    seq = handler_meta["sequence"]
    assert isinstance(seq, list)
    keys = [item.get("key") for item in seq if isinstance(item, dict) and item.get("kind") == "key"]
    assert keys == ["up", "up", "up"]


def test_apply_transcript_triggers_dispatch_type_supports_ctrl_chords() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "type": config.TranscriptVerbConfig(
                action="type",
                enabled=True,
                type="type",
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli type control b d", commands=commands)
    assert out == "ctrl+b d"
    assert meta is not None
    handler_meta = meta["meta"]["handler_meta"]
    seq = handler_meta["sequence"]
    assert seq and isinstance(seq, list)
    first = seq[0]
    assert isinstance(first, dict)
    assert first.get("kind") == "key"
    assert first.get("key") == "b"
    assert first.get("mods") == ["ctrl"]


def test_apply_transcript_triggers_dispatch_type_normalizes_case_and_ctrl_alias() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "type": config.TranscriptVerbConfig(
                action="type",
                enabled=True,
                type="type",
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("Zwingli type CTRL B key", commands=commands)
    assert out == "ctrl+b"
    assert meta is not None
    handler_meta = meta["meta"]["handler_meta"]
    seq = handler_meta["sequence"]
    assert isinstance(seq, list)
    assert seq and seq[0]["kind"] == "key"
    assert seq[0]["key"] == "b"
    assert seq[0]["mods"] == ["ctrl"]


# --- type-verb parser: focused unit tests ---
#
# The three test_apply_transcript_triggers_dispatch_type_* tests above cover
# the dispatch-layer wiring. The tests below call _action_type directly to
# exercise tokenizer/parser edges (filler tokens, separators, multi-word
# keys, chord syntax) without re-piping every case through dispatch.


def _type_keys(meta: dict) -> list[tuple[str, tuple[str, ...]]]:
    return [
        (item["key"], tuple(item.get("mods", [])))
        for item in meta["sequence"]
        if isinstance(item, dict) and item.get("kind") == "key"
    ]


def _type_texts(meta: dict) -> list[str]:
    return [
        item["text"]
        for item in meta["sequence"]
        if isinstance(item, dict) and item.get("kind") == "text"
    ]


def test_type_action_ignores_common_filler_tokens() -> None:
    # Exercises: please, press, the, key, and, then, hit, a, arrow.
    out, meta = tt._action_type("please press the up key and then hit a down arrow")
    assert _type_keys(meta) == [("up", ()), ("down", ())]
    assert out == "up down"


def test_type_action_ignores_less_common_filler_tokens() -> None:
    # Exercises: tap, full, stop, keys, pressed, an, push, hold, release.
    out, meta = tt._action_type("tap full stop keys pressed an enter key push hold release")
    assert _type_keys(meta) == [("enter", ())]
    assert _type_texts(meta) == []
    assert out == "enter"


def test_type_action_ignores_word_form_punctuation_tokens() -> None:
    # comma/colon/semicolon/period are in _TYPE_IGNORE_TOKENS so the model
    # can spell punctuation out as words and still produce clean key events.
    out, meta = tt._action_type("hit escape comma colon semicolon period tab")
    assert _type_keys(meta) == [("esc", ()), ("tab", ())]
    assert out == "esc tab"


def test_type_action_translates_literal_punctuation_to_separators() -> None:
    # _TYPE_TOKEN_TRANSLATION maps , . : ; ! ? ( ) [ ] { } " ' \\ / and
    # whitespace to spaces before splitting.
    out, meta = tt._action_type('up, down. left: right; (enter) [tab] "esc"')
    assert _type_keys(meta) == [
        ("up", ()),
        ("down", ()),
        ("left", ()),
        ("right", ()),
        ("enter", ()),
        ("tab", ()),
        ("esc", ()),
    ]
    assert out == "up down left right enter tab esc"


def test_type_action_handles_hyphen_and_underscore_separators() -> None:
    # Tokenizer replaces - and _ with spaces so "up-arrow", "up_arrow", and
    # "up arrow" all reduce to the up key (with "arrow" as filler).
    out, meta = tt._action_type("up-arrow up_arrow up arrow")
    assert _type_keys(meta) == [("up", ()), ("up", ()), ("up", ())]
    assert out == "up up up"


def test_type_action_recognizes_function_keys_in_range() -> None:
    out, meta = tt._action_type("f1 f12 f24")
    assert _type_keys(meta) == [("f1", ()), ("f12", ()), ("f24", ())]
    assert out == "f1 f12 f24"


def test_type_action_falls_back_to_text_for_out_of_range_function_keys() -> None:
    # The fN parser only accepts 1..24; f0 and f25 fall through to text.
    out, meta = tt._action_type("f0 f25")
    assert _type_keys(meta) == []
    assert _type_texts(meta) == ["f0 f25"]
    assert out == "f0 f25"


def test_type_action_parses_multi_word_keys() -> None:
    out, meta = tt._action_type(
        "new line line break carriage return page up page down back space space bar"
    )
    assert _type_keys(meta) == [
        ("enter", ()),
        ("enter", ()),
        ("enter", ()),
        ("pageup", ()),
        ("pagedown", ()),
        ("backspace", ()),
        ("space", ()),
    ]


def test_type_action_parses_explicit_plus_chord_syntax() -> None:
    out, meta = tt._action_type("shift+a control+enter")
    assert _type_keys(meta) == [
        ("a", ("shift",)),
        ("enter", ("ctrl",)),
    ]
    assert out == "shift+a ctrl+enter"


def test_type_action_explicit_plus_chord_with_unknown_modifier_keeps_modifier_as_text() -> None:
    # Unknown mod prefix is preserved as pending text rather than silently
    # dropped, so the user sees what was misread.
    out, meta = tt._action_type("foo+a")
    assert _type_keys(meta) == [("a", ())]
    assert _type_texts(meta) == ["foo"]
    assert out == "foo a"


def test_type_action_lone_modifier_with_no_following_key_falls_to_text() -> None:
    # A trailing modifier with no key flushes back as the alias text.
    out, meta = tt._action_type("control")
    assert _type_keys(meta) == []
    assert _type_texts(meta) == ["ctrl"]
    assert out == "ctrl"


def test_type_action_empty_prompt_returns_empty_sequence() -> None:
    out, meta = tt._action_type("")
    assert meta["sequence"] == []
    assert out == ""


def test_type_action_all_filler_prompt_returns_empty_sequence() -> None:
    out, meta = tt._action_type("please and the")
    assert meta["sequence"] == []
    assert out == ""


def test_type_action_preserves_text_interleaved_with_keys() -> None:
    out, meta = tt._action_type("hello world enter goodbye")
    seq = meta["sequence"]
    assert [item.get("kind") for item in seq] == ["text", "key", "text"]
    assert seq[0] == {"kind": "text", "text": "hello world"}
    assert seq[1] == {"kind": "key", "key": "enter", "mods": []}
    assert seq[2] == {"kind": "text", "text": "goodbye"}
    assert out == "hello world enter goodbye"


def test_type_action_modifier_applies_to_multi_word_key() -> None:
    out, meta = tt._action_type("control new line")
    assert _type_keys(meta) == [("enter", ("ctrl",))]
    assert out == "ctrl+enter"


def test_type_action_supports_multiple_stacked_modifiers() -> None:
    # Use "b" rather than "a" — "a" is an article and lives in the filler set.
    out, meta = tt._action_type("shift control b")
    assert _type_keys(meta) == [("b", ("shift", "ctrl"))]
    assert out == "shift+ctrl+b"


def test_apply_transcript_triggers_dispatch_shell_timeout_reports_error(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "subprocess": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                timeout_seconds=0.01,
            )
        },
    )

    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"), output="partial\n")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    out, meta = tt.apply_transcript_triggers("zwingli subprocess echo hi", commands=commands)
    assert out == "partial"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "subprocess"
    assert meta["meta"]["action"] == "shell"
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["error"] == "timeout"
    assert handler_meta["returncode"] is None
    assert handler_meta["timeout_seconds"] == 0.01
    assert isinstance(handler_meta["duration_ms"], int)


def test_apply_transcript_triggers_dispatch_llm_profile_applies_template(monkeypatch) -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "bash": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="bash"
            )
        },
        llm_profiles={
            "bash": config.TranscriptLLMProfileConfig(
                model="gpt-test",
                temperature=0.3,
                system_prompt="Write a bash script. Output only the script.",
                user_prompt_template="Write a bash script for: {{text}}",
            )
        },
    )

    seen = {}

    def _fake_process(prompt: str, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return "echo hi", {"provider": "fake", "model": kwargs.get("model")}

    import voicepipe.zwingli as zwingli

    monkeypatch.setattr(zwingli, "process_zwingli_prompt_result", _fake_process)

    out, meta = tt.apply_transcript_triggers("zwingli bash list files", commands=commands)
    assert out == "echo hi"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "bash"
    assert meta["meta"]["action"] == "zwingli"
    assert meta["meta"]["profile"] == "bash"
    assert meta["meta"]["profile_found"] is True
    assert meta["meta"]["template_applied"] is True
    assert seen["prompt"] == "Write a bash script for: list files"
    assert seen["kwargs"]["model"] == "gpt-test"
    assert seen["kwargs"]["temperature"] == 0.3
    assert seen["kwargs"]["system_prompt"] == "Write a bash script. Output only the script."


def test_apply_transcript_triggers_dispatch_disabled_verb_falls_back() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "shell": config.TranscriptVerbConfig(action="shell", enabled=False, type="shell"),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli shell echo hi", commands=commands)
    assert out == "shell echo hi"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "unknown-verb"
    assert meta["meta"]["verb"] == "shell"
    assert meta["meta"]["action"] == "strip"
    assert meta["meta"]["disabled_verb"] == "shell"


def test_apply_transcript_triggers_dispatch_parses_verb_separators() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={"strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin")},
    )
    out, meta = tt.apply_transcript_triggers("Zwingli, strip: hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "verb"


def test_apply_transcript_triggers_dispatch_resolves_multi_word_alias() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "plugin": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                aliases=("plug in",),
            ),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli plug in hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["action"] == "dispatch"
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "plugin"


def test_apply_transcript_triggers_dispatch_resolves_single_word_alias() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "python": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                aliases=("py",),
            ),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli py print hello", commands=commands)
    assert out == "print hello"
    assert meta is not None
    assert meta["meta"]["verb"] == "python"


def test_apply_transcript_triggers_dispatch_alias_with_separator() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "plugin": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                aliases=("plug in",),
            ),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli plug in, hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["meta"]["verb"] == "plugin"


def test_apply_transcript_triggers_chain_pipes_output_to_next_step() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "echo": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin"
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli echo hello world then copy", commands=commands
    )
    assert out == "hello world"
    assert meta is not None
    assert meta["meta"]["verb"] == "copy"
    assert meta["meta"]["action"] == "clipboard"
    # The clipboard verb is now routed via destination metadata; the actual
    # copy is performed by the emission layer (recording.py / fast.py).
    assert meta["meta"]["destination"] == "clipboard"
    chain = meta["meta"]["chain"]
    assert len(chain) == 1
    assert chain[0]["verb"] == "echo"
    assert chain[0]["action"] == "strip"
    # Unified planner shape: chain entries carry the resolved args and the
    # step's output_text so callers don't have to re-pair against the plan.
    assert chain[0]["args"] == "hello world"
    assert chain[0]["output_text"] == "hello world"


def test_apply_transcript_triggers_chain_three_steps() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "echo": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
            "tag": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin"
            ),
        },
    )

    # echo "alpha" -> "alpha"; then tag (no args, pipes "alpha") -> "alpha"; then copy
    out, meta = tt.apply_transcript_triggers(
        "zwingli echo alpha then tag then copy", commands=commands
    )
    assert out == "alpha"
    assert meta is not None
    assert meta["meta"]["destination"] == "clipboard"
    chain = meta["meta"]["chain"]
    assert [step["verb"] for step in chain] == ["echo", "tag"]


def test_apply_transcript_triggers_chain_keyword_without_known_verb_is_inline(
    monkeypatch,
) -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "echo": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
        },
    )

    # "then summarize" is not a chain: "summarize" is not a registered verb,
    # so the whole thing stays in the echo args.
    out, meta = tt.apply_transcript_triggers(
        "zwingli echo rewrite this then summarize", commands=commands
    )
    assert out == "rewrite this then summarize"
    assert meta is not None
    assert "chain" not in meta["meta"]


def test_apply_transcript_triggers_chain_step_with_explicit_args_ignores_pipe(
    monkeypatch,
) -> None:
    seen_inputs: list[str] = []

    def _spy_strip(
        prompt,
        *,
        verb_cfg=None,
        profiles=None,
        captures=None,
        commands=None,
        actuator=None,
    ):
        seen_inputs.append(prompt)
        return (prompt or "").strip(), {}

    monkeypatch.setitem(tt._ACTIONS, "strip", _spy_strip)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "echo": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
            "tag": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli echo alpha then tag beta", commands=commands
    )
    # Step 1 was called with "alpha"; step 2 was called with "beta" (its own
    # args), not "alpha" (the pipe).
    assert seen_inputs == ["alpha", "beta"]
    assert out == "beta"


def test_apply_transcript_triggers_dispatch_clipboard_marks_destination() -> None:
    """The clipboard action is a passthrough; routing is via meta.destination.
    The actual copy is performed by the emission layer (recording.py / fast.py).
    """
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin"
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli copy hello world", commands=commands)
    assert out == "hello world"
    assert meta is not None
    assert meta["meta"]["verb"] == "copy"
    assert meta["meta"]["action"] == "clipboard"
    assert meta["meta"]["destination"] == "clipboard"


def test_apply_transcript_triggers_dispatch_clipboard_empty_prompt() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin"
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli copy", commands=commands)
    assert out == ""
    assert meta["meta"]["destination"] == "clipboard"


def test_apply_transcript_triggers_dispatch_alias_does_not_shadow_existing_verb() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "keep": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin"),
            "drop": config.TranscriptVerbConfig(
                action="strip", enabled=True, type="builtin", aliases=("keep",)
            ),
        },
    )
    out, meta = tt.apply_transcript_triggers("zwingli keep hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["meta"]["verb"] == "keep"
    assert meta["meta"]["action"] == "strip"


def test_apply_transcript_triggers_dispatch_plugin_disabled(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "upper.py").write_text(
        "def handle(text: str) -> str:\n"
        "    return (text or '').upper()\n",
        encoding="utf-8",
    )

    def _fake_config_dir(*, create: bool = False):
        del create
        return tmp_path

    monkeypatch.setattr(config, "config_dir", _fake_config_dir)
    monkeypatch.delenv("VOICEPIPE_PLUGIN_ALLOW", raising=False)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "upper": config.TranscriptVerbConfig(
                action="plugin",
                enabled=True,
                type="plugin",
                plugin=config.TranscriptPluginConfig(
                    path="plugins/upper.py",
                    callable="handle",
                ),
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli upper hello", commands=commands)
    assert out.startswith("⚠ zwingli: ")
    assert "VOICEPIPE_PLUGIN_ALLOW" in out
    assert meta is not None
    assert meta["ok"] is False
    assert "VOICEPIPE_PLUGIN_ALLOW" in meta["error"]
    assert meta["meta"]["error_destination"] == "type"


def test_apply_transcript_triggers_dispatch_plugin_executes(monkeypatch, tmp_path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "upper.py").write_text(
        "def handle(text: str) -> str:\n"
        "    return (text or '').upper()\n",
        encoding="utf-8",
    )

    def _fake_config_dir(*, create: bool = False):
        del create
        return tmp_path

    monkeypatch.setattr(config, "config_dir", _fake_config_dir)
    monkeypatch.setenv("VOICEPIPE_PLUGIN_ALLOW", "1")

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "upper": config.TranscriptVerbConfig(
                action="plugin",
                enabled=True,
                type="plugin",
                plugin=config.TranscriptPluginConfig(
                    path="plugins/upper.py",
                    callable="handle",
                ),
            )
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli upper hello", commands=commands)
    assert out == "HELLO"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "upper"
    assert meta["meta"]["action"] == "plugin"
    assert meta["meta"]["plugin"]["path"] == "plugins/upper.py"
    assert meta["meta"]["plugin"]["callable"] == "handle"
    handler_meta = meta["meta"]["handler_meta"]
    assert isinstance(handler_meta["duration_ms"], int)


# --- error_destination behavior ---


def _commands_with_error_destination(destination: str) -> "config.TranscriptCommandsConfig":
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination=destination
        ),
        verbs={
            "boom": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell"
            )
        },
    )


def test_apply_transcript_triggers_error_default_destination_types_no_clipboard(
    monkeypatch,
) -> None:
    # Shell verb without VOICEPIPE_SHELL_ALLOW raises; should produce a typed
    # error and NOT touch the clipboard.
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)
    copied: list[str] = []

    def _fake_copy(text: str) -> tuple[bool, str | None]:
        copied.append(text)
        return True, None

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    commands = _commands_with_error_destination("type")
    out, meta = tt.apply_transcript_triggers("zwingli boom echo hi", commands=commands)

    assert out.startswith("⚠ zwingli: ")
    assert "VOICEPIPE_SHELL_ALLOW" in out
    assert meta is not None
    assert meta["ok"] is False
    assert meta["meta"]["error_destination"] == "type"
    assert "suppress_type" not in meta["meta"]
    assert "clipboard" not in meta["meta"]
    assert copied == []


def test_apply_transcript_triggers_error_destination_clipboard_suppresses_typing(
    monkeypatch,
) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)
    copied: list[str] = []

    def _fake_copy(text: str) -> tuple[bool, str | None]:
        copied.append(text)
        return True, None

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    commands = _commands_with_error_destination("clipboard")
    out, meta = tt.apply_transcript_triggers("zwingli boom echo hi", commands=commands)

    assert out.startswith("⚠ zwingli: ")
    assert meta is not None
    assert meta["meta"]["error_destination"] == "clipboard"
    assert meta["meta"]["suppress_type"] is True
    assert meta["meta"]["clipboard"] is True
    assert copied == [out]


def test_apply_transcript_triggers_error_destination_both_types_and_copies(
    monkeypatch,
) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)
    copied: list[str] = []

    def _fake_copy(text: str) -> tuple[bool, str | None]:
        copied.append(text)
        return True, None

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    commands = _commands_with_error_destination("both")
    out, meta = tt.apply_transcript_triggers("zwingli boom echo hi", commands=commands)

    assert out.startswith("⚠ zwingli: ")
    assert meta is not None
    assert meta["meta"]["error_destination"] == "both"
    assert "suppress_type" not in meta["meta"]  # still types
    assert meta["meta"]["clipboard"] is True
    assert copied == [out]


def test_apply_transcript_triggers_error_destination_clipboard_failure_still_yields_error_text(
    monkeypatch,
) -> None:
    # If copy_to_clipboard returns failure, the error text still flows back
    # to the caller — we don't silently swallow.
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)

    def _fake_copy(text: str) -> tuple[bool, str | None]:
        return False, "clipboard backend missing"

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    commands = _commands_with_error_destination("clipboard")
    out, meta = tt.apply_transcript_triggers("zwingli boom echo hi", commands=commands)

    assert out.startswith("⚠ zwingli: ")
    assert meta is not None
    assert meta["meta"]["error_destination"] == "clipboard"
    assert meta["meta"]["clipboard"] is False
    assert meta["meta"]["suppress_type"] is True


def test_apply_transcript_triggers_unknown_action_honors_error_destination(
    monkeypatch,
) -> None:
    # Non-dispatch failure path (unknown action). Pass commands explicitly so
    # the destination flag is consulted from there rather than disk.
    copied: list[str] = []

    def _fake_copy(text: str) -> tuple[bool, str | None]:
        copied.append(text)
        return True, None

    import voicepipe.clipboard as clipboard_mod

    monkeypatch.setattr(clipboard_mod, "copy_to_clipboard", _fake_copy)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingly": "nope"},
        dispatch=config.TranscriptDispatchConfig(
            unknown_verb="strip", error_destination="clipboard"
        ),
    )
    out, meta = tt.apply_transcript_triggers("zwingly do it", commands=commands)

    assert out.startswith("⚠ zwingli: ")
    assert "Unknown transcript trigger action" in out
    assert meta is not None
    assert meta["ok"] is False
    assert meta["meta"]["error_destination"] == "clipboard"
    assert meta["meta"]["suppress_type"] is True
    assert copied == [out]


def test_format_zwingli_error_text_uses_prefix() -> None:
    assert tt._format_zwingli_error_text("boom").startswith("⚠ zwingli: ")
    assert tt._format_zwingli_error_text("boom") == "⚠ zwingli: boom"
    # Empty reason still produces a recognizable prefix.
    assert tt._format_zwingli_error_text("").startswith("⚠ zwingli")


# --- pattern compilation and matching ---


def test_compile_verb_pattern_single_placeholder() -> None:
    compiled, names = tt._compile_verb_pattern("google {query}")
    assert names == ("query",)
    m = compiled.match("google how to make sourdough")
    assert m is not None
    assert m.group("query") == "how to make sourdough"


def test_compile_verb_pattern_multi_placeholder() -> None:
    compiled, names = tt._compile_verb_pattern("search {query} on {site}")
    assert names == ("query", "site")
    m = compiled.match("search rust async on hackernews")
    assert m is not None
    assert m.group("query") == "rust async"
    assert m.group("site") == "hackernews"


def test_compile_verb_pattern_case_insensitive_literals() -> None:
    compiled, _ = tt._compile_verb_pattern("set timer for {minutes} minutes")
    assert compiled.match("SET TIMER FOR 5 MINUTES") is not None
    assert compiled.match("Set Timer For 10 minutes") is not None


def test_compile_verb_pattern_flexible_whitespace() -> None:
    compiled, _ = tt._compile_verb_pattern("set timer for {minutes} minutes")
    assert compiled.match("set  timer   for 5 minutes") is not None
    assert compiled.match("set\ttimer for 5 minutes") is not None


def test_compile_verb_pattern_requires_non_empty_capture() -> None:
    compiled, _ = tt._compile_verb_pattern("google {query}")
    assert compiled.match("google ") is None
    assert compiled.match("google") is None


def test_compile_verb_pattern_duplicate_capture_name_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        tt._compile_verb_pattern("from {x} to {x}")


def test_compile_verb_pattern_escapes_regex_metachars() -> None:
    # The "." in "google.com" must match literally, not any-char.
    compiled, _ = tt._compile_verb_pattern("open google.com")
    assert compiled.match("open google.com") is not None
    assert compiled.match("open googleXcom") is None


def test_find_pattern_match_returns_first_match() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "timer": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                pattern="set timer for {minutes} minutes",
            ),
            "google": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                pattern="google {query}",
            ),
        },
    )

    result = tt._find_pattern_match("google rust async", commands=commands)
    assert result is not None
    verb, captures = result
    assert verb == "google"
    assert captures == {"query": "rust async"}


def test_find_pattern_match_skips_disabled_verbs() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "timer": config.TranscriptVerbConfig(
                action="shell",
                enabled=False,  # disabled
                type="shell",
                pattern="set timer for {minutes} minutes",
            ),
        },
    )
    assert tt._find_pattern_match("set timer for 5 minutes", commands=commands) is None


def test_find_pattern_match_skips_verbs_without_pattern() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "plain": config.TranscriptVerbConfig(action="strip", enabled=True),
        },
    )
    assert tt._find_pattern_match("plain hello", commands=commands) is None


# --- pattern dispatch end-to-end ---


def test_apply_transcript_triggers_pattern_substitutes_shell_command_template(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")
    captured: list[str] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "timer": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                pattern="set timer for {minutes} minutes",
                command_template="sleep {minutes}m && notify-send Timer",
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli set timer for 5 minutes", commands=commands
    )
    assert out == "ok"
    assert captured == ["sleep 5m && notify-send Timer"]
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["verb"] == "timer"
    assert meta["meta"]["captures"] == {"minutes": "5"}


def test_apply_transcript_triggers_pattern_wins_over_name_match(
    monkeypatch,
) -> None:
    """If a chunk matches a verb's pattern, that verb runs even if the first
    word would resolve to a different verb."""
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")
    captured: list[str] = []

    def _fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            # If we name-matched, "set" would not resolve and we'd hit
            # unknown_verb=strip. The pattern below ensures the timer verb wins.
            "timer": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                pattern="set timer for {minutes} minutes",
                command_template="sleep {minutes}m",
            ),
            "set": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli set timer for 7 minutes", commands=commands
    )
    assert meta is not None
    assert meta["meta"]["verb"] == "timer"
    assert captured == ["sleep 7m"]


def test_apply_transcript_triggers_pattern_falls_back_to_name_match() -> None:
    """If no pattern matches, name-based dispatch still works."""
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "timer": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                pattern="set timer for {minutes} minutes",
            ),
            "echo": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers("zwingli echo hello", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["meta"]["verb"] == "echo"
    assert "captures" not in meta["meta"]


def test_apply_transcript_triggers_pattern_substitutes_llm_template(
    monkeypatch,
) -> None:
    seen: dict[str, str] = {}

    def _fake_process(prompt, **kwargs):
        seen["prompt"] = prompt
        return "result", {"provider": "fake"}

    import voicepipe.zwingli as zwingli

    monkeypatch.setattr(zwingli, "process_zwingli_prompt_result", _fake_process)

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "rewrite": config.TranscriptVerbConfig(
                action="zwingli",
                enabled=True,
                type="llm",
                profile="rewrite",
                pattern="rewrite {style}: {text}",
            ),
        },
        llm_profiles={
            "rewrite": config.TranscriptLLMProfileConfig(
                system_prompt="Rewrite text.",
                user_prompt_template="In {{style}} style, rewrite: {{text}}",
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli rewrite formal: hello world", commands=commands
    )
    assert out == "result"
    assert seen["prompt"] == "In formal style, rewrite: hello world"
    assert meta is not None
    assert meta["meta"]["verb"] == "rewrite"
    assert meta["meta"]["captures"] == {"style": "formal", "text": "hello world"}


def test_render_user_prompt_template_leaves_unknown_placeholders_literal() -> None:
    rendered = tt._render_user_prompt_template(
        "Hello {{name}}, your text: {{text}}", text="howdy", captures={"name": "Alice"}
    )
    assert rendered == "Hello Alice, your text: howdy"

    # Unknown placeholder remains literal.
    rendered2 = tt._render_user_prompt_template(
        "User {{name}} said: {{text}}", text="hi", captures={}
    )
    assert rendered2 == "User {{name}} said: hi"


def test_apply_transcript_triggers_pattern_exposes_captures_for_clipboard() -> None:
    """Verbs without a template still expose captures in meta."""
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "note": config.TranscriptVerbConfig(
                action="clipboard",
                enabled=True,
                type="builtin",
                pattern="note {what}",
            ),
        },
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli note buy more coffee", commands=commands
    )
    assert out == "note buy more coffee"
    assert meta is not None
    assert meta["meta"]["verb"] == "note"
    assert meta["meta"]["captures"] == {"what": "buy more coffee"}
    # Destination metadata is what tells the emission layer to copy.
    assert meta["meta"]["destination"] == "clipboard"


# --- help verb ---


def _commands_with_verbs(verbs: dict[str, config.TranscriptVerbConfig]) -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=verbs,
    )


def test_help_verb_no_args_lists_backend_and_verbs(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_BACKEND", "openai")
    monkeypatch.setenv("VOICEPIPE_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
    commands = _commands_with_verbs(
        {
            "help": config.TranscriptVerbConfig(action="help", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin",
                aliases=("clip", "paste"),
            ),
            "shell": config.TranscriptVerbConfig(
                action="zwingli", enabled=True, type="llm", profile="shell"
            ),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli help", commands=commands)
    assert "backend: openai" in out
    assert "model: gpt-4o-transcribe" in out
    assert "copy" in out
    assert "shell" in out
    assert "clip, paste" in out
    assert "destination" not in meta["meta"]  # help verb has no destination by default
    assert meta["meta"]["handler_meta"]["help_target"] is None


def test_help_verb_with_known_verb_shows_details() -> None:
    commands = _commands_with_verbs(
        {
            "help": config.TranscriptVerbConfig(action="help", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin",
                aliases=("clip",),
            ),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli help copy", commands=commands)
    assert out.startswith("copy:")
    assert "action: clipboard" in out
    assert "destination: clipboard" in out  # default destination for clipboard action
    assert "aliases: clip" in out
    assert meta["meta"]["handler_meta"]["help_target"] == "copy"


def test_help_verb_resolves_alias() -> None:
    commands = _commands_with_verbs(
        {
            "help": config.TranscriptVerbConfig(action="help", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin",
                aliases=("clip",),
            ),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli help clip", commands=commands)
    assert out.startswith("copy:")
    assert meta["meta"]["handler_meta"]["help_target"] == "copy"


def test_help_verb_unknown_returns_friendly_error() -> None:
    commands = _commands_with_verbs(
        {
            "help": config.TranscriptVerbConfig(action="help", enabled=True, type="builtin"),
            "copy": config.TranscriptVerbConfig(action="clipboard", enabled=True, type="builtin"),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli help flarble", commands=commands)
    assert "unknown verb" in out.lower()
    assert "flarble" in out
    assert "copy" in out  # lists known verbs
    assert meta["meta"]["handler_meta"]["help_unknown"] is True


def test_help_verb_injected_when_not_in_user_config(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {"strip": {"type": "builtin"}},
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    loaded = cfg.get_transcript_commands_config(load_env=False)
    assert "help" in loaded.verbs
    assert loaded.verbs["help"].action == "help"
    assert loaded.verbs["help"].type == "builtin"


def test_help_verb_user_override_wins(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {"help": {"type": "builtin", "action": "strip"}},
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    loaded = cfg.get_transcript_commands_config(load_env=False)
    assert loaded.verbs["help"].action == "strip"


# --- confirm flow (shell/execute + yes/no) ---


@pytest.fixture
def pending_in_tmp(tmp_path, monkeypatch):
    """Redirect pending storage to a temp file for the duration of one test."""
    from pathlib import Path

    import voicepipe.pending as pending_mod

    path = tmp_path / "pending-command.json"

    def _fake_path(*, create_dir: bool = False) -> Path:
        if create_dir:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(pending_mod, "pending_path", _fake_path)
    return path


def _confirm_commands(verbs: dict[str, config.TranscriptVerbConfig]) -> config.TranscriptCommandsConfig:
    full = {
        "yes": config.TranscriptVerbConfig(action="yes", enabled=True, type="builtin"),
        "no": config.TranscriptVerbConfig(action="no", enabled=True, type="builtin"),
    }
    full.update(verbs)
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=full,
    )


def test_shell_with_confirm_stashes_pending_and_does_not_run(pending_in_tmp, monkeypatch) -> None:
    ran: list[str] = []

    def _fake_run(*args, **kwargs):
        ran.append(args[0] if args else "")
        return "shouldnotrun", "", {}

    monkeypatch.setattr(tt._shell, "_run_shell_command", _fake_run)

    commands = _confirm_commands(
        {
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell", confirm=True
            ),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli subprocess ls -la", commands=commands)
    assert "Pending shell" in out
    assert "ls -la" in out
    assert ran == []
    assert meta["meta"]["handler_meta"]["pending"] is True
    assert pending_in_tmp.exists()


def test_execute_with_confirm_stashes_pending_and_skips_enter(pending_in_tmp) -> None:
    commands = _confirm_commands(
        {
            "execute": config.TranscriptVerbConfig(
                action="execute", enabled=True, type="execute", confirm=True
            ),
        }
    )
    out, meta = tt.apply_transcript_triggers("zwingli execute echo hi", commands=commands)
    assert "Pending execute" in out
    assert "echo hi" in out
    assert meta["meta"]["handler_meta"]["pending"] is True
    # confirm path doesn't request Enter (that happens on yes)
    assert meta["meta"]["handler_meta"].get("enter") is not True


def test_yes_after_shell_confirm_runs_original(pending_in_tmp, monkeypatch) -> None:
    ran: list[str] = []

    def _fake_run(command, *, timeout_seconds=None, actuator=None):
        ran.append(command)
        return f"ran:{command}\n", "", {"returncode": 0}

    monkeypatch.setattr(tt._shell, "_run_shell_command", _fake_run)

    commands = _confirm_commands(
        {
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell", confirm=True
            ),
        }
    )
    tt.apply_transcript_triggers("zwingli subprocess ls -la", commands=commands)
    assert pending_in_tmp.exists()

    out, meta = tt.apply_transcript_triggers("zwingli yes", commands=commands)
    assert ran == ["ls -la"]
    assert "ran:ls -la" in out
    assert meta["meta"]["handler_meta"]["resumed_pending"] is True
    assert not pending_in_tmp.exists()


def test_yes_after_execute_confirm_returns_command_with_enter(pending_in_tmp) -> None:
    commands = _confirm_commands(
        {
            "execute": config.TranscriptVerbConfig(
                action="execute", enabled=True, type="execute", confirm=True
            ),
        }
    )
    tt.apply_transcript_triggers("zwingli execute echo hi", commands=commands)
    out, meta = tt.apply_transcript_triggers("zwingli yes", commands=commands)
    assert out == "echo hi"
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["enter"] is True
    assert handler_meta["resumed_pending"] is True
    assert not pending_in_tmp.exists()


def test_yes_without_pending_returns_friendly_message(pending_in_tmp) -> None:
    commands = _confirm_commands({})
    out, meta = tt.apply_transcript_triggers("zwingli yes", commands=commands)
    assert "no pending" in out.lower()
    assert meta["meta"]["handler_meta"]["no_pending"] is True


def test_no_cancels_pending(pending_in_tmp, monkeypatch) -> None:
    monkeypatch.setattr(tt._shell, "_run_shell_command", lambda *a, **k: ("", "", {}))

    commands = _confirm_commands(
        {
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell", confirm=True
            ),
        }
    )
    tt.apply_transcript_triggers("zwingli subprocess rm -rf tmp", commands=commands)
    assert pending_in_tmp.exists()

    out, meta = tt.apply_transcript_triggers("zwingli no", commands=commands)
    assert "cancelled" in out.lower()
    assert "rm -rf tmp" in out
    assert meta["meta"]["handler_meta"]["cancelled"] is True
    assert not pending_in_tmp.exists()


def test_no_without_pending_returns_friendly_message(pending_in_tmp) -> None:
    commands = _confirm_commands({})
    out, meta = tt.apply_transcript_triggers("zwingli no", commands=commands)
    assert "no pending" in out.lower()
    assert meta["meta"]["handler_meta"]["no_pending"] is True


def test_second_confirm_replaces_first(pending_in_tmp, monkeypatch) -> None:
    monkeypatch.setattr(tt._shell, "_run_shell_command", lambda *a, **k: ("", "", {}))

    commands = _confirm_commands(
        {
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell", confirm=True
            ),
        }
    )
    tt.apply_transcript_triggers("zwingli subprocess ls", commands=commands)
    tt.apply_transcript_triggers("zwingli subprocess pwd", commands=commands)

    import voicepipe.pending as pending_mod

    entry = pending_mod.load_pending()
    assert entry is not None
    assert entry.command == "pwd"


def test_yes_and_no_verbs_auto_injected(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {"strip": {"type": "builtin"}},
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    loaded = cfg.get_transcript_commands_config(load_env=False)
    assert "yes" in loaded.verbs and loaded.verbs["yes"].action == "yes"
    assert "no" in loaded.verbs and loaded.verbs["no"].action == "no"


def test_confirm_field_rejects_non_bool(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "sub": {"type": "shell", "enabled": True, "confirm": "yes"},
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(cfg.VoicepipeConfigError) as exc_info:
        cfg._load_transcript_commands_json()
    assert "confirm" in str(exc_info.value)


# ---------- Codegen verb (LLM-generated script run by an interpreter) ----------


def _codegen_commands(
    verb_name: str,
    verb_cfg: config.TranscriptVerbConfig,
    *,
    profile: config.TranscriptLLMProfileConfig | None = None,
) -> config.TranscriptCommandsConfig:
    profiles: dict[str, config.TranscriptLLMProfileConfig] = {}
    if profile is not None and verb_cfg.profile:
        profiles[verb_cfg.profile] = profile
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            verb_name: verb_cfg,
            "yes": config.TranscriptVerbConfig(action="yes", enabled=True, type="builtin"),
            "no": config.TranscriptVerbConfig(action="no", enabled=True, type="builtin"),
        },
        llm_profiles=profiles,
    )


def test_codegen_calls_llm_then_runs_interpreter_on_tempfile(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    import voicepipe.zwingli as zwingli

    def _fake_process(prompt: str, **kwargs):
        return "print('hi')\n", {"backend": "fake", "model": kwargs.get("model")}

    monkeypatch.setattr(zwingli, "process_zwingli_prompt_result", _fake_process)

    captured: dict = {}

    def _fake_run(argv, **kwargs):
        captured["argv"] = argv
        from pathlib import Path

        captured["script"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="hi\n", stderr=""
        )

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    commands = _codegen_commands(
        "pyrun",
        config.TranscriptVerbConfig(
            action="codegen",
            enabled=True,
            type="codegen",
            profile="pyscript",
            interpreter="python3",
        ),
        profile=config.TranscriptLLMProfileConfig(
            system_prompt="Write a Python script. Output only code.",
            user_prompt_template="Write a Python script for: {{text}}",
        ),
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli pyrun say hi to the user", commands=commands
    )
    assert out == "hi"
    assert meta["ok"] is True
    handler_meta = meta["meta"]["handler_meta"]
    assert handler_meta["interpreter"] == "python3"
    assert handler_meta["returncode"] == 0
    assert handler_meta["generated_script"] == "print('hi')"
    assert captured["argv"][0] == "python3"
    assert captured["script"] == "print('hi')"


def test_codegen_strips_markdown_fences_from_llm_output(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    import voicepipe.zwingli as zwingli

    fenced = "```python\nprint('hi')\n```"
    monkeypatch.setattr(
        zwingli,
        "process_zwingli_prompt_result",
        lambda prompt, **kw: (fenced, {}),
    )

    captured: dict = {}

    def _fake_run(argv, **kwargs):
        from pathlib import Path

        captured["script"] = Path(argv[1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    commands = _codegen_commands(
        "pyrun",
        config.TranscriptVerbConfig(
            action="codegen",
            enabled=True,
            type="codegen",
            interpreter="python3",
        ),
    )
    tt.apply_transcript_triggers("zwingli pyrun anything", commands=commands)
    assert captured["script"] == "print('hi')"


def test_codegen_blocked_when_shell_not_allowed(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)

    import voicepipe.zwingli as zwingli

    monkeypatch.setattr(
        zwingli,
        "process_zwingli_prompt_result",
        lambda prompt, **kw: ("print('hi')", {}),
    )

    commands = _codegen_commands(
        "pyrun",
        config.TranscriptVerbConfig(
            action="codegen",
            enabled=True,
            type="codegen",
            interpreter="python3",
        ),
    )
    out, meta = tt.apply_transcript_triggers("zwingli pyrun do it", commands=commands)
    assert meta["ok"] is False
    assert "Codegen execution is disabled" in (meta.get("error") or "")
    assert out.startswith("⚠ zwingli")


def test_codegen_with_confirm_stashes_generated_script_then_yes_runs_it(
    pending_in_tmp, monkeypatch
) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    import voicepipe.zwingli as zwingli

    monkeypatch.setattr(
        zwingli,
        "process_zwingli_prompt_result",
        lambda prompt, **kw: ("print('hello')", {}),
    )

    runs: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        from pathlib import Path

        runs.append([argv[0], Path(argv[1]).read_text(encoding="utf-8")])
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="hello\n", stderr=""
        )

    monkeypatch.setattr(tt.subprocess, "run", _fake_run)

    commands = _codegen_commands(
        "pyrun",
        config.TranscriptVerbConfig(
            action="codegen",
            enabled=True,
            type="codegen",
            interpreter="python3",
            confirm=True,
        ),
    )

    out, meta = tt.apply_transcript_triggers(
        "zwingli pyrun greet the user", commands=commands
    )
    assert "Pending python3 script" in out
    assert "print('hello')" in out
    assert runs == []
    assert pending_in_tmp.exists()

    import voicepipe.pending as pending_mod

    entry = pending_mod.load_pending()
    assert entry is not None
    assert entry.verb_type == "script"
    assert entry.interpreter == "python3"
    assert entry.command == "print('hello')"

    out2, meta2 = tt.apply_transcript_triggers("zwingli yes", commands=commands)
    assert out2 == "hello"
    assert runs == [["python3", "print('hello')"]]
    handler_meta = meta2["meta"]["handler_meta"]
    assert handler_meta["resumed_pending"] is True
    assert handler_meta["interpreter"] == "python3"
    assert not pending_in_tmp.exists()


def test_codegen_config_requires_interpreter(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "pyrun": {"type": "codegen", "enabled": True},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    with pytest.raises(cfg.VoicepipeConfigError) as exc_info:
        cfg._load_transcript_commands_json()
    assert "interpreter" in str(exc_info.value)


def test_apply_transcript_triggers_fires_audio_feedback_for_success_and_error(
    monkeypatch,
) -> None:
    """End-to-end: dispatch routes a success payload to play('success') and an
    error payload to play('error') via the _maybe_play_audio_feedback hook."""
    import voicepipe.audio_feedback as af

    fired: list[str] = []
    monkeypatch.setattr(af, "play", lambda event: fired.append(event))

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={"strip": config.TranscriptVerbConfig(action="strip", enabled=True, type="builtin")},
    )

    tt.apply_transcript_triggers("zwingli hello", commands=commands)
    assert fired == ["success"]

    fired.clear()
    # Force an exception by patching the action handler to raise.
    monkeypatch.setitem(
        tt._ACTIONS,
        "strip",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    tt.apply_transcript_triggers("zwingli hello", commands=commands)
    assert fired == ["error"]


def test_apply_transcript_triggers_fires_pending_event_for_confirm_stash(
    pending_in_tmp, monkeypatch
) -> None:
    import voicepipe.audio_feedback as af

    fired: list[str] = []
    monkeypatch.setattr(af, "play", lambda event: fired.append(event))

    commands = _confirm_commands(
        {
            "subprocess": config.TranscriptVerbConfig(
                action="shell", enabled=True, type="shell", confirm=True
            ),
        }
    )
    tt.apply_transcript_triggers("zwingli subprocess ls", commands=commands)
    assert fired == ["pending"]


# ---------- Per-verb rate limits ----------


def test_verb_rate_limit_exceeded_returns_friendly_error(monkeypatch) -> None:
    import voicepipe.rate_limit as rl

    rl.reset_for_tests()

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                rate_limit_per_min=2,
            ),
        },
    )

    out1, meta1 = tt.apply_transcript_triggers("zwingli strip hello", commands=commands)
    assert meta1["ok"] is True
    assert meta1["meta"]["rate_limit_per_min"] == 2

    out2, meta2 = tt.apply_transcript_triggers("zwingli strip there", commands=commands)
    assert meta2["ok"] is True

    out3, meta3 = tt.apply_transcript_triggers("zwingli strip friend", commands=commands)
    assert meta3["ok"] is False
    assert "rate limit exceeded" in meta3["error"]
    assert "strip" in meta3["error"]
    assert out3.startswith("⚠ zwingli")
    rl.reset_for_tests()


def test_verb_rate_limit_zero_does_not_throttle(monkeypatch) -> None:
    import voicepipe.rate_limit as rl

    rl.reset_for_tests()

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip",
                enabled=True,
                type="builtin",
                rate_limit_per_min=0,
            ),
        },
    )
    for _ in range(20):
        out, meta = tt.apply_transcript_triggers("zwingli strip x", commands=commands)
        assert meta["ok"] is True


def test_rate_limit_per_min_rejects_non_int(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "sub": {"type": "shell", "enabled": True, "rate_limit_per_min": "5"},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    with pytest.raises(cfg.VoicepipeConfigError) as exc_info:
        cfg._load_transcript_commands_json()
    assert "rate_limit_per_min" in str(exc_info.value)


def test_rate_limit_per_min_rejects_negative(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "sub": {"type": "shell", "enabled": True, "rate_limit_per_min": -3},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    with pytest.raises(cfg.VoicepipeConfigError) as exc_info:
        cfg._load_transcript_commands_json()
    assert "rate_limit_per_min" in str(exc_info.value)


def test_rate_limit_per_min_accepts_int(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "sub": {"type": "shell", "enabled": True, "rate_limit_per_min": 5},
                },
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    loaded = cfg.get_transcript_commands_config(load_env=False)
    assert loaded.verbs["sub"].rate_limit_per_min == 5


def test_apply_transcript_triggers_skips_audio_when_no_trigger_matches(
    monkeypatch,
) -> None:
    import voicepipe.audio_feedback as af

    fired: list[str] = []
    monkeypatch.setattr(af, "play", lambda event: fired.append(event))

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={},
    )
    out, meta = tt.apply_transcript_triggers("plain dictation, no trigger", commands=commands)
    assert meta is None
    assert fired == []


# ---------- dry_run_dispatch ----------


def _dry_run_commands() -> config.TranscriptCommandsConfig:
    return config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "strip": config.TranscriptVerbConfig(
                action="strip", enabled=True, type="builtin"
            ),
            "subprocess": config.TranscriptVerbConfig(
                action="shell",
                enabled=True,
                type="shell",
                timeout_seconds=10,
                rate_limit_per_min=5,
            ),
            "execute": config.TranscriptVerbConfig(
                action="execute", enabled=True, type="execute"
            ),
            "python": config.TranscriptVerbConfig(
                action="codegen",
                enabled=True,
                type="codegen",
                interpreter="python3",
                profile="python",
                confirm=True,
                aliases=("py", "in python"),
            ),
            "open_in_vim": config.TranscriptVerbConfig(
                action="execute",
                enabled=True,
                type="execute",
                pattern="open {target} in vim",
                command_template="vim {target}",
            ),
            "disabled_shell": config.TranscriptVerbConfig(
                action="shell", enabled=False, type="shell"
            ),
            "copy": config.TranscriptVerbConfig(
                action="clipboard", enabled=True, type="builtin"
            ),
        },
        llm_profiles={
            "python": config.TranscriptLLMProfileConfig(
                temperature=0.0,
                system_prompt="You are a Python generator.",
                user_prompt_template="Write a Python 3 script for: {{text}}",
            ),
        },
    )


def test_dry_run_returns_no_trigger_match_for_plain_text() -> None:
    trace = tt.dry_run_dispatch("just plain text", commands=_dry_run_commands())
    assert trace["trigger_match"] is None
    assert trace["outcome"] == "no_trigger_matched"


def test_dry_run_returns_trigger_action_for_strip_trigger() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "strip"},
        dispatch=config.TranscriptDispatchConfig(),
        verbs={},
    )
    trace = tt.dry_run_dispatch("zwingli hello world", commands=commands)
    assert trace["trigger_match"]["action"] == "strip"
    assert trace["trigger_match"]["remainder"] == "hello world"
    assert trace["outcome"] == "trigger_action"
    assert trace["trigger_action"] == "strip"
    assert "steps" not in trace


def test_dry_run_resolves_direct_verb_and_renders_llm_prompt() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli python count files", commands=_dry_run_commands()
    )
    step = trace["steps"][0]
    assert step["resolution"] == "verb"
    assert step["verb"] == "python"
    assert step["args"] == "count files"
    cfg = step["verb_config"]
    assert cfg["interpreter"] == "python3"
    assert cfg["confirm"] is True
    assert cfg["llm_preview"]["user_prompt"] == "Write a Python 3 script for: count files"
    assert cfg["llm_preview"]["system_prompt"] == "You are a Python generator."


def test_dry_run_resolves_multi_word_alias() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli in python count files", commands=_dry_run_commands()
    )
    step = trace["steps"][0]
    assert step["resolution"] == "verb"
    assert step["verb"] == "python"
    assert step["args"] == "count files"


def test_dry_run_pattern_match_exposes_captures_and_renders_command_template() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli open config.py in vim", commands=_dry_run_commands()
    )
    step = trace["steps"][0]
    assert step["resolution"] == "pattern"
    assert step["verb"] == "open_in_vim"
    assert step["captures"] == {"target": "config.py"}
    cfg = step["verb_config"]
    assert cfg["command_template"] == "vim {target}"
    assert cfg["rendered_command"] == "vim config.py"
    assert cfg["would_type"] == "vim config.py"
    assert cfg["would_press_enter"] is True


def test_dry_run_chain_marks_piped_steps() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli subprocess ls then python", commands=_dry_run_commands()
    )
    assert trace["chain_length"] == 2
    assert trace["chain_uses_pipe"] is True
    step1, step2 = trace["steps"]
    assert step1["verb"] == "subprocess"
    assert step1.get("piped_from_previous") is None
    assert step2["verb"] == "python"
    assert step2["piped_from_previous"] is True
    assert "piped from previous" in step2["args"]
    # The piped marker should also flow into the LLM user prompt preview.
    assert "piped from previous" in step2["verb_config"]["llm_preview"]["user_prompt"]


def test_dry_run_chain_with_explicit_args_does_not_mark_pipe() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli subprocess ls then subprocess pwd", commands=_dry_run_commands()
    )
    assert trace["chain_length"] == 2
    step2 = trace["steps"][1]
    assert step2.get("piped_from_previous") is None
    assert step2["args"] == "pwd"


def test_dry_run_unknown_verb_falls_back_to_dispatch_unknown_verb() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli nonsenseverb hi", commands=_dry_run_commands()
    )
    step = trace["steps"][0]
    assert step["resolution"] == "unknown_verb"
    assert step["verb"] == "nonsenseverb"
    assert step["fallback_action"] == "strip"
    assert "verb_config" not in step


def test_dry_run_disabled_verb_marks_disabled() -> None:
    trace = tt.dry_run_dispatch(
        "zwingli disabled_shell ls", commands=_dry_run_commands()
    )
    step = trace["steps"][0]
    assert step["resolution"] == "disabled_verb"
    assert step["verb"] == "disabled_shell"
    assert step["fallback_action"] == "strip"


def test_dry_run_clipboard_action_shows_implicit_destination() -> None:
    trace = tt.dry_run_dispatch("zwingli copy hello", commands=_dry_run_commands())
    cfg = trace["steps"][0]["verb_config"]
    assert cfg["destination"] == "clipboard"


def test_dry_run_shell_with_confirm_shows_would_stash() -> None:
    commands = _dry_run_commands()
    # Build a fresh commands with confirm on subprocess
    verbs = dict(commands.verbs)
    verbs["subprocess"] = config.TranscriptVerbConfig(
        action="shell", enabled=True, type="shell", timeout_seconds=10, confirm=True
    )
    commands = config.TranscriptCommandsConfig(
        triggers=commands.triggers,
        dispatch=commands.dispatch,
        verbs=verbs,
        llm_profiles=commands.llm_profiles,
    )
    trace = tt.dry_run_dispatch(
        "zwingli subprocess ls", commands=commands
    )
    cfg = trace["steps"][0]["verb_config"]
    assert cfg["would_run_shell"] == "ls"
    assert cfg["would_stash_pending"] is True


def test_dry_run_codegen_with_missing_profile_flags_it() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(),
        verbs={
            "ghost": config.TranscriptVerbConfig(
                action="codegen",
                enabled=True,
                type="codegen",
                interpreter="bash",
                profile="not_defined",
            ),
        },
        llm_profiles={},
    )
    trace = tt.dry_run_dispatch("zwingli ghost do thing", commands=commands)
    cfg = trace["steps"][0]["verb_config"]
    assert cfg.get("llm_profile_missing") == "not_defined"
    assert "llm_preview" not in cfg


def test_dry_run_does_not_invoke_handlers_or_touch_state(monkeypatch) -> None:
    """Sanity: dry_run_dispatch must not call action handlers, the rate limiter,
    the LLM, or any side-effecting subsystem."""
    import voicepipe.rate_limit as rl
    import voicepipe.audio_feedback as af
    import voicepipe.pending as pending_mod
    import voicepipe.zwingli as zwingli

    handler_calls: list = []
    for name in list(tt._ACTIONS):
        monkeypatch.setitem(
            tt._ACTIONS,
            name,
            lambda *a, name=name, **k: handler_calls.append(name) or ("", {}),
        )
    rate_calls: list = []
    monkeypatch.setattr(rl, "check_and_record", lambda *a, **k: rate_calls.append(a))
    audio_calls: list = []
    monkeypatch.setattr(af, "play", lambda *a, **k: audio_calls.append(a))
    pending_calls: list = []
    monkeypatch.setattr(pending_mod, "save_pending", lambda *a, **k: pending_calls.append(a))
    llm_calls: list = []
    monkeypatch.setattr(
        zwingli,
        "process_zwingli_prompt_result",
        lambda *a, **k: llm_calls.append(a) or ("", {}),
    )

    tt.dry_run_dispatch(
        "zwingli subprocess ls then python", commands=_dry_run_commands()
    )

    assert handler_calls == []
    assert rate_calls == []
    assert audio_calls == []
    assert pending_calls == []
    assert llm_calls == []


def test_codegen_config_accepts_interpreter(tmp_path, monkeypatch) -> None:
    import json

    import voicepipe.config as cfg

    monkeypatch.delenv("VOICEPIPE_TRANSCRIPT_TRIGGERS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))

    triggers_path = cfg.config_dir(create=True) / "triggers.json"
    triggers_path.write_text(
        json.dumps(
            {
                "version": 1,
                "triggers": {"zwingli": {"action": "dispatch"}},
                "verbs": {
                    "pyrun": {
                        "type": "codegen",
                        "enabled": True,
                        "interpreter": "python3",
                        "profile": "pyscript",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    cfg.invalidate_transcript_commands_cache()
    loaded = cfg.get_transcript_commands_config(load_env=False)
    pyrun = loaded.verbs["pyrun"]
    assert pyrun.action == "codegen"
    assert pyrun.interpreter == "python3"
    assert pyrun.profile == "pyscript"
    assert pyrun.enabled is True


# ---------- "did you mean?" suggestions for unknown verbs ----------


def _suggest_verbs_map(
    *, include_disabled: bool = False, include_multiword_alias: bool = False
) -> dict[str, config.TranscriptVerbConfig]:
    verbs: dict[str, config.TranscriptVerbConfig] = {
        "python": config.TranscriptVerbConfig(
            action="codegen",
            enabled=True,
            type="codegen",
            aliases=("py",) + (("in python",) if include_multiword_alias else ()),
        ),
        "bash": config.TranscriptVerbConfig(
            action="codegen", enabled=True, type="codegen"
        ),
        "strip": config.TranscriptVerbConfig(
            action="strip", enabled=True, type="builtin"
        ),
    }
    if include_disabled:
        verbs["disabled_shell"] = config.TranscriptVerbConfig(
            action="shell", enabled=False, type="shell"
        )
    return verbs


def test_suggest_verb_returns_close_typo() -> None:
    from voicepipe.transcript_triggers._dispatch import _suggest_verb

    assert _suggest_verb("pyhon", _suggest_verbs_map()) == ["python"]
    assert _suggest_verb("strp", _suggest_verbs_map()) == ["strip"]


def test_suggest_verb_returns_empty_for_unrelated_input() -> None:
    from voicepipe.transcript_triggers._dispatch import _suggest_verb

    assert _suggest_verb("", _suggest_verbs_map()) == []
    assert _suggest_verb("xyzqrs", _suggest_verbs_map()) == []


def test_suggest_verb_maps_single_token_alias_to_canonical() -> None:
    from voicepipe.transcript_triggers._dispatch import _suggest_verb

    # "p" is close to alias "py" — should resolve to canonical "python", not "py".
    suggestions = _suggest_verb("py", _suggest_verbs_map())
    assert suggestions == ["python"]


def test_suggest_verb_excludes_disabled_verbs() -> None:
    from voicepipe.transcript_triggers._dispatch import _suggest_verb

    # A typo close to a disabled verb name should NOT suggest it.
    verbs = _suggest_verbs_map(include_disabled=True)
    suggestions = _suggest_verb("disabld_shell", verbs)
    assert "disabled_shell" not in suggestions


def test_suggest_verb_excludes_multi_token_aliases() -> None:
    from voicepipe.transcript_triggers._dispatch import _suggest_verb

    # "in python" is a multi-token alias; a token typo like "in pythn" wouldn't
    # arrive here anyway (single-token verb extraction). Multi-token aliases
    # shouldn't pollute the single-token suggestion pool.
    verbs = _suggest_verbs_map(include_multiword_alias=True)
    suggestions = _suggest_verb("in", verbs)
    assert "in python" not in suggestions


def test_apply_transcript_triggers_unknown_verb_includes_did_you_mean() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=_suggest_verbs_map(),
    )
    _out, meta = tt.apply_transcript_triggers(
        "zwingli pyhon print hello", commands=commands
    )
    assert meta is not None
    assert meta["meta"]["mode"] == "unknown-verb"
    assert meta["meta"]["verb"] == "pyhon"
    assert meta["meta"]["did_you_mean"] == ["python"]


def test_apply_transcript_triggers_known_verb_has_no_did_you_mean() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=_suggest_verbs_map(),
    )
    _out, meta = tt.apply_transcript_triggers(
        "zwingli strip hello", commands=commands
    )
    assert meta is not None
    assert "did_you_mean" not in meta["meta"]


def test_dry_run_unknown_verb_includes_did_you_mean() -> None:
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=_suggest_verbs_map(),
    )
    trace = tt.dry_run_dispatch("zwingli pyhon print hello", commands=commands)
    step = trace["steps"][0]
    assert step["resolution"] == "unknown_verb"
    assert step["verb"] == "pyhon"
    assert step["did_you_mean"] == ["python"]


def test_dry_run_disabled_verb_includes_did_you_mean_when_match_exists() -> None:
    # If user types the exact disabled verb name, the suggestion list should
    # contain other ENABLED verbs that are close (none in this map are, so
    # the field is absent — proves we don't echo back the disabled name).
    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs=_suggest_verbs_map(include_disabled=True),
    )
    trace = tt.dry_run_dispatch(
        "zwingli disabled_shell hi", commands=commands
    )
    step = trace["steps"][0]
    assert step["resolution"] == "disabled_verb"
    assert step["verb"] == "disabled_shell"
    # disabled_shell is the only thing close to itself, but it's excluded.
    assert step.get("did_you_mean") in (None, [])
