"""Persist and replay the last Voicepipe output text.

This supports "oops I typed into the wrong window" workflows by keeping the
final output text in a small local buffer so it can be replayed without
re-transcribing audio.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from voicepipe.paths import runtime_app_dir
from voicepipe.platform import is_windows


_LAST_TEXT_FILENAME = "voicepipe-last.txt"
_LAST_JSON_FILENAME = "voicepipe-last.json"
_LAST_VERSION = 1


@dataclass(frozen=True)
class LastOutput:
    text: str
    created_ms: int
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "version": _LAST_VERSION,
            "created_ms": int(self.created_ms),
            "text": self.text,
        }
        if self.payload is not None:
            out["payload"] = self.payload
        return out


def last_output_text_path(*, create_dir: bool = False) -> Path:
    return runtime_app_dir(create=create_dir) / _LAST_TEXT_FILENAME


def last_output_json_path(*, create_dir: bool = False) -> Path:
    return runtime_app_dir(create=create_dir) / _LAST_JSON_FILENAME


def _atomic_write(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    try:
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass


def _ensure_private_file(path: Path) -> None:
    if is_windows():
        return
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def save_last_output(
    text: str,
    *,
    payload: Mapping[str, Any] | None = None,
    created_ms: int | None = None,
) -> LastOutput:
    created = int(time.time() * 1000) if created_ms is None else int(created_ms)
    normalized = (text or "").rstrip("\n")
    entry = LastOutput(
        text=normalized,
        created_ms=created,
        payload=dict(payload) if payload is not None else None,
    )

    # Always write a plain-text file for quick manual inspection.
    txt_path = last_output_text_path(create_dir=True)
    _atomic_write(txt_path, entry.text + "\n")
    _ensure_private_file(txt_path)

    json_path = last_output_json_path(create_dir=True)
    _atomic_write(json_path, json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    _ensure_private_file(json_path)
    return entry


def load_last_output() -> LastOutput | None:
    json_path = last_output_json_path(create_dir=False)
    try:
        if json_path.exists():
            raw = json_path.read_text(encoding="utf-8").strip()
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and str(parsed.get("version")) == str(_LAST_VERSION):
                    created_ms = int(parsed.get("created_ms") or 0)
                    text = str(parsed.get("text") or "")
                    payload = parsed.get("payload")
                    return LastOutput(
                        text=text,
                        created_ms=created_ms,
                        payload=payload if isinstance(payload, dict) else None,
                    )
    except Exception:
        pass

    txt_path = last_output_text_path(create_dir=False)
    try:
        if not txt_path.exists():
            return None
        text = txt_path.read_text(encoding="utf-8").rstrip("\n")
        created_ms = int(txt_path.stat().st_mtime * 1000)
        return LastOutput(text=text, created_ms=created_ms, payload=None)
    except Exception:
        return None


def clear_last_output() -> None:
    for path in (last_output_json_path(create_dir=False), last_output_text_path(create_dir=False)):
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

