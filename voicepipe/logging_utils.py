"""Logging configuration helpers for Voicepipe."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional


_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s[%(process)d]: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _parse_log_level(level: Optional[str], *, default: int) -> int:
    if not level:
        return default
    normalized = str(level).strip().upper()
    if normalized.isdigit():
        try:
            return int(normalized)
        except ValueError:
            return default
    parsed = logging.getLevelName(normalized)
    if isinstance(parsed, int):
        return parsed
    return default


def configure_logging(*, debug: bool = False, default_level: int = logging.INFO) -> None:
    """Configure process-wide logging with env/flag control.

    Precedence:
      1) `--debug` (when passed in) enables DEBUG.
      2) `VOICEPIPE_LOG_LEVEL` env var overrides defaults.
      3) `default_level` is used otherwise.
    """
    env_level = os.environ.get("VOICEPIPE_LOG_LEVEL")
    if debug:
        level = logging.DEBUG
    else:
        level = _parse_log_level(env_level, default=default_level)

    root = logging.getLogger()
    root.setLevel(level)

    if root.handlers:
        # Avoid adding duplicate handlers; just update existing ones.
        for handler in root.handlers:
            handler.setLevel(level)
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, datefmt=_DEFAULT_DATEFMT))
    root.addHandler(handler)
