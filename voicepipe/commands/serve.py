"""``voicepipe serve`` — run the Zwingli dispatch server."""
from __future__ import annotations

import sys

import click


@click.command("serve")
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help=(
        "Interface to bind. Non-loopback hosts require "
        "VOICEPIPE_DISPATCH_TOKEN to be set."
    ),
)
@click.option(
    "--port",
    default=8765,
    show_default=True,
    type=int,
    help="Port to bind.",
)
def serve(host: str, port: int) -> None:
    """Run the Zwingli dispatch server (HTTP wrapper around the dispatcher).

    Exposes POST /dispatch, GET /triggers, GET /log/tail, GET /health.

    Requires the 'server' extra: pip install 'voicepipe[server]'
    """
    try:
        from voicepipe.dispatch_server import run
    except RuntimeError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    try:
        run(host=host, port=port)
    except RuntimeError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(2)
