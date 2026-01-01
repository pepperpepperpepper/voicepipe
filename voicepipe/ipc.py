"""IPC helpers for talking to the voicepipe daemon over a Unix socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Dict, Optional

from .paths import daemon_socket_paths


class IpcError(RuntimeError):
    pass


class IpcUnavailable(IpcError):
    pass


class IpcTimeout(IpcError):
    pass


class IpcProtocolError(IpcError):
    pass


def _read_json_message(sock: socket.socket, *, max_bytes: int) -> bytes:
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except socket.timeout as e:
            raise IpcTimeout("Timed out waiting for daemon response") from e
        if not chunk:
            break
        data += chunk
        if len(data) > max_bytes:
            raise IpcProtocolError(f"Daemon response too large (>{max_bytes} bytes)")
        try:
            json.loads(data.decode())
            return data
        except json.JSONDecodeError:
            continue
    if not data:
        raise IpcProtocolError("Daemon returned an empty response")
    return data


def send_request(
    command: str,
    *,
    socket_path: Optional[Path] = None,
    connect_timeout: float = 0.5,
    read_timeout: Optional[float] = None,
    max_response_bytes: int = 65536,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Send a single JSON request to the daemon and return its JSON response."""
    if not command:
        raise ValueError("command must be non-empty")

    sock_paths = [socket_path] if socket_path is not None else daemon_socket_paths()
    existing_paths: list[Path] = []
    for p in sock_paths:
        try:
            if p.exists():
                existing_paths.append(p)
        except Exception:
            continue
    if not existing_paths:
        tried = ", ".join(str(p) for p in sock_paths)
        raise IpcUnavailable(f"Daemon socket not found (tried: {tried})")

    if read_timeout is None:
        read_timeout = 0.5 if command == "status" else 5.0

    request = {"command": command, **kwargs}

    last_error: Exception | None = None
    for sock_path in existing_paths:
        client: Optional[socket.socket] = None
        try:
            try:
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            except OSError as e:
                raise IpcUnavailable(f"Unix sockets are unavailable on this platform: {e}") from e
            client.settimeout(connect_timeout)
            try:
                client.connect(str(sock_path))
            except OSError as e:
                last_error = e
                continue

            payload = (json.dumps(request) + "\n").encode()
            try:
                client.sendall(payload)
            except OSError as e:
                last_error = e
                continue

            client.settimeout(read_timeout)
            response_bytes = _read_json_message(client, max_bytes=max_response_bytes)
            try:
                return json.loads(response_bytes.decode())
            except json.JSONDecodeError as e:
                raise IpcProtocolError(f"Invalid JSON response from daemon: {e}") from e
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    msg = f"Could not connect to daemon (tried: {', '.join(str(p) for p in existing_paths)})"
    if last_error is not None:
        msg = f"{msg}: {last_error}"
    raise IpcUnavailable(msg)


def try_send_request(
    command: str,
    *,
    socket_path: Optional[Path] = None,
    connect_timeout: float = 0.5,
    read_timeout: Optional[float] = None,
    max_response_bytes: int = 65536,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Best-effort daemon request.

    Returns:
      - dict: daemon response (or {"error": "..."} on protocol/timeouts)
      - None: daemon unavailable (socket missing / cannot connect), caller may fall back
    """
    try:
        return send_request(
            command,
            socket_path=socket_path,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            max_response_bytes=max_response_bytes,
            **kwargs,
        )
    except IpcUnavailable:
        return None
    except IpcError as e:
        return {"error": str(e)}
