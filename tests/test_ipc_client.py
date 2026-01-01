from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest
import sys

from voicepipe.ipc import IpcProtocolError, IpcUnavailable, send_request, try_send_request

if sys.platform == "win32":  # pragma: no cover
    pytest.skip("AF_UNIX integration tests are skipped on Windows CI", allow_module_level=True)


def _start_ipc_server(socket_path: Path, handler) -> threading.Thread:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def _run() -> None:
        try:
            conn, _ = server.accept()
            try:
                handler(conn)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        finally:
            try:
                server.close()
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def test_send_request_round_trip(tmp_path: Path) -> None:
    sock_path = tmp_path / "voicepipe.sock"

    def handler(conn: socket.socket) -> None:
        data = b""
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        req = json.loads(data.decode("utf-8"))
        assert req["command"] == "status"
        conn.sendall(b'{"ok": true}')

    t = _start_ipc_server(sock_path, handler)
    out = send_request("status", socket_path=sock_path, connect_timeout=1.0, read_timeout=1.0)
    t.join(timeout=1.0)
    assert out == {"ok": True}


def test_send_request_rejects_oversize_response(tmp_path: Path) -> None:
    sock_path = tmp_path / "voicepipe.sock"

    def handler(conn: socket.socket) -> None:
        _ = conn.recv(4096)
        conn.sendall(b"x" * 11)

    _start_ipc_server(sock_path, handler)
    with pytest.raises(IpcProtocolError):
        send_request(
            "status",
            socket_path=sock_path,
            connect_timeout=1.0,
            read_timeout=1.0,
            max_response_bytes=10,
        )


def test_try_send_request_returns_none_when_socket_missing(tmp_path: Path) -> None:
    assert try_send_request("status", socket_path=tmp_path / "missing.sock") is None


def test_send_request_raises_when_socket_missing(tmp_path: Path) -> None:
    with pytest.raises(IpcUnavailable):
        send_request("status", socket_path=tmp_path / "missing.sock")
