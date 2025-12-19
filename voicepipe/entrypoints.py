"""Console-script entrypoints that wrap the main Click CLI.

These exist to provide stable command names without duplicating logic.
"""

from __future__ import annotations

import sys
from typing import Optional


def transcribe_file_main(argv: Optional[list[str]] = None) -> None:
    """Entry point for the `voicepipe-transcribe-file` console script."""
    from voicepipe.cli import main as cli_main

    args = list(sys.argv[1:] if argv is None else argv)
    # Note: group-level options like `--debug` are available via `voicepipe`.
    cli_main.main(args=["transcribe-file", *args], prog_name="voicepipe-transcribe-file")

