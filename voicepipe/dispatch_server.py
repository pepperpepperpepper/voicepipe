"""Zwingli dispatch server — HTTP wrapper around the Zwingli dispatcher.

Runs the same ``apply_transcript_triggers`` a desktop voicepipe daemon would
run, but as a small FastAPI service so thin clients (the planned Android
"Zwangli" app, a browser extension, a smartwatch app, etc.) can share the
brain without embedding Python.

Endpoints
---------

- ``POST /dispatch``  — run a transcript through the dispatcher.
- ``GET  /triggers``  — resolved ``triggers.json`` (for client-side hints).
- ``GET  /log/tail?n=N`` — recent JSON-line debug events.
- ``GET  /health``    — liveness.

Auth
----

Bearer token via ``VOICEPIPE_DISPATCH_TOKEN``. When unset, :func:`run`
refuses non-loopback binds so an unauthenticated server doesn't end up on
the open internet by accident.

Capabilities
------------

The ``POST /dispatch`` request body may include a ``capabilities`` list
(e.g. ``["clipboard", "audio_feedback"]``) advertising what the client
itself can do. :class:`ServerActuator` filters its capability set
accordingly and the dispatcher's graceful-skip path takes over for verbs
the client can't support — a phone without a shell sees ``zwingli
subprocess …`` resolve to a polite ``⚠ zwingli`` notice rather than the
server running the shell on the user's behalf.

The server installation is optional. Install with::

    pip install 'voicepipe[server]'
"""
from __future__ import annotations

import dataclasses
import hmac
import os
from pathlib import Path
from typing import Any

import voicepipe.transcript_triggers as tt
from voicepipe.commands.triggers import _read_debug_log_tail
from voicepipe.config import get_transcript_commands_config
from voicepipe.transcript_triggers._actuator import (
    CAP_AUDIO_FEEDBACK,
    CAP_CLIPBOARD,
    CAP_SUBPROCESS,
    DesktopActuator,
    SubprocessResult,
)
from voicepipe.transcript_triggers._debug_log import _zwingli_debug_log_path


_ALL_CAPS: frozenset[str] = frozenset(
    {CAP_SUBPROCESS, CAP_CLIPBOARD, CAP_AUDIO_FEEDBACK}
)


class ServerActuator:
    """Dispatch-server actuator.

    - ``run_subprocess`` runs on the server (delegates to a real
      :class:`DesktopActuator`).
    - ``set_clipboard`` and ``play_feedback`` queue ``client_actions``
      entries so the calling client can execute them locally.
    - Capabilities are intersected with the set the client advertised;
      verbs whose requirements the client doesn't support graceful-skip
      via the dispatcher's standard error path.
    """

    def __init__(self, *, capabilities: set[str] | None = None) -> None:
        if capabilities is None:
            self._caps = _ALL_CAPS
        else:
            self._caps = frozenset(c for c in capabilities if c in _ALL_CAPS)
        self._desktop = DesktopActuator()
        self.client_actions: list[dict[str, Any]] = []

    def capabilities(self) -> frozenset[str]:
        return self._caps

    def run_subprocess(
        self,
        argv: list[str] | str,
        *,
        shell: bool = False,
        timeout_seconds: float | None = None,
    ) -> SubprocessResult:
        return self._desktop.run_subprocess(
            argv, shell=shell, timeout_seconds=timeout_seconds
        )

    def set_clipboard(self, text: str) -> bool:
        if CAP_CLIPBOARD not in self._caps:
            return False
        self.client_actions.append({"type": "clipboard", "text": text})
        return True

    def play_feedback(self, event: str) -> None:
        if CAP_AUDIO_FEEDBACK not in self._caps:
            return
        self.client_actions.append({"type": "feedback", "event": event})


try:
    from pydantic import BaseModel as _PydanticBaseModel
except ImportError:  # pragma: no cover — exercised by the smoke message below
    _PydanticBaseModel = None  # type: ignore[assignment,misc]


def _require_fastapi() -> None:
    try:
        import fastapi  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "voicepipe dispatch server requires the 'server' extra: "
            "pip install 'voicepipe[server]'"
        ) from e


if _PydanticBaseModel is not None:

    class DispatchRequest(_PydanticBaseModel):  # type: ignore[misc,valid-type]
        transcript: str
        session_id: str | None = None
        capabilities: list[str] | None = None

    class DispatchResponse(_PydanticBaseModel):  # type: ignore[misc,valid-type]
        ok: bool
        output_text: str
        payload: dict[str, Any] | None = None
        client_actions: list[dict[str, Any]] = []


def _resolve_token(token: str | None) -> str | None:
    if token is not None:
        return token.strip() or None
    env = (os.environ.get("VOICEPIPE_DISPATCH_TOKEN") or "").strip()
    return env or None


def create_app(*, token: str | None = None):
    """Build the FastAPI ``app``.

    The returned object is a standard FastAPI application — mount it in
    a Uvicorn worker, an ASGI runner of your choice, or Starlette's
    :class:`~starlette.testclient.TestClient` for tests.

    ``token`` defaults to ``VOICEPIPE_DISPATCH_TOKEN`` from the
    environment. When set, every endpoint except ``/health`` requires
    ``Authorization: Bearer <token>``.
    """
    _require_fastapi()

    from fastapi import Depends, FastAPI, Header, HTTPException

    resolved_token = _resolve_token(token)

    app = FastAPI(title="Zwingli dispatch", version="1")

    def _check_auth(authorization: str | None = Header(default=None)) -> None:
        if resolved_token is None:
            return
        expected = f"Bearer {resolved_token}"
        if authorization is None or not hmac.compare_digest(
            authorization, expected
        ):
            raise HTTPException(
                status_code=401, detail="invalid or missing bearer token"
            )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "auth_required": resolved_token is not None}

    @app.post(
        "/dispatch",
        response_model=DispatchResponse,
        dependencies=[Depends(_check_auth)],
    )
    def dispatch(req: DispatchRequest) -> DispatchResponse:
        caps = set(req.capabilities) if req.capabilities is not None else None
        actuator = ServerActuator(capabilities=caps)
        commands = get_transcript_commands_config(load_env=False)
        output_text, payload = tt.apply_transcript_triggers(
            req.transcript, commands=commands, actuator=actuator
        )
        return DispatchResponse(
            ok=bool(payload is None or payload.get("ok", True)),
            output_text=output_text,
            payload=payload,
            client_actions=actuator.client_actions,
        )

    @app.get("/triggers", dependencies=[Depends(_check_auth)])
    def triggers() -> dict[str, Any]:
        commands = get_transcript_commands_config(load_env=False)
        return {
            "triggers": dict(commands.triggers),
            "dispatch": dataclasses.asdict(commands.dispatch),
            "verbs": {
                n: dataclasses.asdict(commands.verbs[n])
                for n in sorted(commands.verbs)
            },
            "profiles": {
                n: dataclasses.asdict(commands.llm_profiles[n])
                for n in sorted(commands.llm_profiles)
            },
        }

    @app.get("/log/tail", dependencies=[Depends(_check_auth)])
    def log_tail(n: int = 20) -> dict[str, Any]:
        path: Path = _zwingli_debug_log_path()
        if not path.exists():
            return {"events": [], "path": str(path)}
        return {"events": _read_debug_log_tail(path, n), "path": str(path)}

    return app


def run(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    token: str | None = None,
) -> None:
    """Run the dispatch server with uvicorn.

    Refuses to bind a non-loopback host without a bearer token to keep an
    unauthenticated server off the open internet. Pass ``token=`` or set
    ``VOICEPIPE_DISPATCH_TOKEN`` to enable bearer auth.
    """
    _require_fastapi()
    try:
        import uvicorn
    except ImportError as e:
        raise RuntimeError(
            "uvicorn missing; install with 'pip install voicepipe[server]'"
        ) from e

    resolved_token = _resolve_token(token)
    if resolved_token is None and host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(
            f"Refusing to bind {host!r} without VOICEPIPE_DISPATCH_TOKEN. "
            "Set it to a strong secret, or bind 127.0.0.1 only."
        )

    app = create_app(token=resolved_token)
    uvicorn.run(app, host=host, port=port, log_level="info")
