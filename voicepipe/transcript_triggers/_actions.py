"""The action-handler dispatch table.

Each ``_action_*`` handler lives in a subsystem module
(``_basic``, ``_llm``, ``_shell``, ``_codegen``, ``_type``, ``_plugin``,
``_help``, ``_pending``). This file just collects them into the
``_ACTIONS`` dict that the dispatcher consults.

The dict is mutable on purpose — tests use ``monkeypatch.setitem`` to
swap a handler temporarily, and that pattern relies on a single shared
dict object reachable via ``voicepipe.transcript_triggers._ACTIONS``.
"""

from __future__ import annotations

from typing import Any, Callable

from ._basic import _action_clipboard, _action_strip
from ._codegen import _action_codegen
from ._help import _action_help
from ._llm import _action_zwingli
from ._pending import _action_no, _action_yes
from ._plugin import _action_plugin
from ._shell import _action_execute, _action_shell
from ._type import _action_type


ActionHandler = Callable[..., tuple[str, dict[str, Any]]]

_ACTIONS: dict[str, ActionHandler] = {
    "strip": _action_strip,
    "zwingli": _action_zwingli,
    "shell": _action_shell,
    "execute": _action_execute,
    "type": _action_type,
    "plugin": _action_plugin,
    "clipboard": _action_clipboard,
    "codegen": _action_codegen,
    "help": _action_help,
    "yes": _action_yes,
    "no": _action_no,
}

# Keys returned by handlers in their inner_meta that the dispatcher should
# surface at the top level of verb metadata rather than under "handler_meta".
_PROMOTED_META_KEYS: tuple[str, ...] = ("profile_found", "template_applied")
