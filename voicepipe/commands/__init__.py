"""Click command groups for the Voicepipe CLI."""

from __future__ import annotations

import click

from .config import config_group
from .doctor import doctor_group, doctor_legacy
from .recording import cancel, daemon, dictate, start, status, stop, transcribe_file
from .replay import replay
from . import smoke as smoke_module
from .service import service_group
from .setup import setup
from .launchd import launchd_group
from .hotkey import hotkey_group


def register(main: click.Group) -> None:
    main.add_command(config_group)
    main.add_command(service_group)
    main.add_command(launchd_group)
    main.add_command(hotkey_group)
    main.add_command(setup)
    main.add_command(doctor_group)
    main.add_command(doctor_legacy)
    main.add_command(smoke_module.smoke)

    main.add_command(start)
    main.add_command(stop)
    main.add_command(dictate)
    main.add_command(status)
    main.add_command(cancel)
    main.add_command(transcribe_file)
    main.add_command(daemon)
    main.add_command(replay)
