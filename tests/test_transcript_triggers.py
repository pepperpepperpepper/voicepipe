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
    assert out == "do it"
    assert meta is not None
    assert meta["ok"] is False
    assert meta["error"]


def test_apply_transcript_triggers_shell_disabled(monkeypatch) -> None:
    monkeypatch.delenv("VOICEPIPE_SHELL_ALLOW", raising=False)
    out, meta = tt.apply_transcript_triggers("zwingli echo hi", triggers={"zwingli": "shell"})
    assert out == "echo hi"
    assert meta is not None
    assert meta["ok"] is False
    assert "VOICEPIPE_SHELL_ALLOW" in meta["error"]


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


def test_apply_transcript_triggers_dispatch_shell_uses_verb_timeout_seconds(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")
    monkeypatch.setenv("VOICEPIPE_SHELL_TIMEOUT_SECONDS", "99")

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
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

    out, meta = tt.apply_transcript_triggers("zwingli execute echo hi", commands=commands)
    assert out == "hello"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
    assert meta["meta"]["verb"] == "execute"
    assert meta["meta"]["action"] == "shell"
    assert meta["meta"]["timeout_seconds"] == 5.0
    assert meta["meta"]["handler_meta"]["timeout_seconds"] == 5.0


def test_apply_transcript_triggers_dispatch_shell_timeout_reports_error(monkeypatch) -> None:
    monkeypatch.setenv("VOICEPIPE_SHELL_ALLOW", "1")

    commands = config.TranscriptCommandsConfig(
        triggers={"zwingli": "dispatch"},
        dispatch=config.TranscriptDispatchConfig(unknown_verb="strip"),
        verbs={
            "execute": config.TranscriptVerbConfig(
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

    out, meta = tt.apply_transcript_triggers("zwingli execute echo hi", commands=commands)
    assert out == "partial"
    assert meta is not None
    assert meta["ok"] is True
    assert meta["meta"]["mode"] == "verb"
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
    assert out == "upper hello"
    assert meta is not None
    assert meta["ok"] is False
    assert "VOICEPIPE_PLUGIN_ALLOW" in meta["error"]


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
