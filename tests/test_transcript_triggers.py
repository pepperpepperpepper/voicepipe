from __future__ import annotations

import subprocess

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

    def _spy_strip(prompt, *, verb_cfg=None, profiles=None, captures=None):
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
