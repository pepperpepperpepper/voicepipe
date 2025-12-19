from __future__ import annotations

import json

import pytest

from voicepipe.ipc import IpcProtocolError, _read_json_message


class _FakeSock:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    def recv(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_read_json_message_handles_partial_chunks() -> None:
    sock = _FakeSock([b'{"ok":', b" true}"])
    out = _read_json_message(sock, max_bytes=1024)
    assert json.loads(out.decode()) == {"ok": True}


def test_read_json_message_rejects_oversize_response() -> None:
    sock = _FakeSock([b"x" * 11])
    with pytest.raises(IpcProtocolError):
        _read_json_message(sock, max_bytes=10)
