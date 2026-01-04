"""`voicepipe hotkey …` helpers.

On macOS, the most native way to bind a global keyboard shortcut without third
party hotkey apps is a Quick Action (Automator service). This module provides
an opt-in helper to install such a workflow bundle.
"""

from __future__ import annotations

import plistlib
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import click

from voicepipe.platform import is_macos


@dataclass(frozen=True)
class QuickActionWorkflow:
    name: str
    command: str


def _services_dir() -> Path:
    return Path.home() / "Library" / "Services"


def _workflow_dir(workflow: QuickActionWorkflow) -> Path:
    # Automator services are bundles named "<Name>.workflow".
    return _services_dir() / f"{workflow.name}.workflow"


def _render_info_plist(workflow: QuickActionWorkflow) -> bytes:
    payload = {
        "NSServices": [
            {
                "NSBackgroundColorName": "background",
                "NSIconName": "NSActionTemplate",
                "NSMenuItem": {"default": workflow.name},
                "NSMessage": "runWorkflowAsService",
            }
        ]
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _render_document_wflow(workflow: QuickActionWorkflow) -> bytes:
    run_shell_script_action = {
        "AMAccepts": {"Container": "List", "Optional": True, "Types": ["com.apple.cocoa.string"]},
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {
            "COMMAND_STRING": {},
            "CheckedForUserDefaultShell": {},
            "inputMethod": {},
            "shell": {},
            "source": {},
        },
        "AMProvides": {"Container": "List", "Types": ["com.apple.cocoa.string"]},
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": workflow.command,
            "CheckedForUserDefaultShell": True,
            "inputMethod": 0,
            "shell": "/bin/bash",
            "source": "",
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CFBundleVersion": "2.0.3",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "Class Name": "RunShellScriptAction",
        "InputUUID": str(uuid.uuid4()).upper(),
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "OutputUUID": str(uuid.uuid4()).upper(),
        "UUID": str(uuid.uuid4()).upper(),
        "UnlocalizedApplications": ["Automator"],
        "arguments": {
            "0": {
                "default value": 0,
                "name": "inputMethod",
                "required": "0",
                "type": "0",
                "uuid": "0",
            },
            "1": {
                "default value": False,
                "name": "CheckedForUserDefaultShell",
                "required": "0",
                "type": "0",
                "uuid": "1",
            },
            "2": {
                "default value": "",
                "name": "source",
                "required": "0",
                "type": "0",
                "uuid": "2",
            },
            "3": {
                "default value": "",
                "name": "COMMAND_STRING",
                "required": "0",
                "type": "0",
                "uuid": "3",
            },
            "4": {
                "default value": "/bin/sh",
                "name": "shell",
                "required": "0",
                "type": "0",
                "uuid": "4",
            },
        },
        "conversionLabel": 0,
        "isViewVisible": 1,
        "location": "309.000000:305.000000",
        "nibPath": (
            "/System/Library/Automator/Run Shell Script.action/Contents/Resources/"
            "Base.lproj/main.nib"
        ),
    }

    payload = {
        "AMApplicationBuild": "523",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [{"action": run_shell_script_action, "isViewVisible": 1}],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.nothing",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            "presentationMode": 11,
            "processesInput": False,
            "serviceInputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": False,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": False,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=False)


def _safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return
    except Exception as e:
        raise click.ClickException(f"Failed to remove {path}: {e}") from e


@click.group(name="hotkey", invoke_without_command=True)
@click.pass_context
def hotkey_group(ctx: click.Context) -> None:
    """Hotkey helpers (macOS Quick Action installer)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@hotkey_group.command("install")
@click.option("--name", default="Voicepipe Toggle", show_default=True)
@click.option(
    "--python",
    "python_path",
    default=None,
    help="Python executable to use (default: current interpreter)",
)
@click.option("--force", is_flag=True, help="Overwrite existing workflow if present")
def hotkey_install(name: str, python_path: str | None, force: bool) -> None:
    """Install a macOS Quick Action workflow (assign shortcut in System Settings)."""
    if not is_macos():
        raise click.ClickException("Hotkey installer is only available on macOS.")

    workflow_name = (name or "").strip() or "Voicepipe Toggle"
    python = (python_path or sys.executable).strip() or sys.executable

    # Use an explicit interpreter so we don't depend on PATH in Automator.
    command = f"\"{python}\" -m voicepipe.fast toggle"

    workflow = QuickActionWorkflow(name=workflow_name, command=command)
    bundle_dir = _workflow_dir(workflow)
    contents_dir = bundle_dir / "Contents"

    if bundle_dir.exists() and not force:
        raise click.ClickException(
            f"Workflow already exists: {bundle_dir} (use --force to overwrite)"
        )

    if bundle_dir.exists():
        _safe_rmtree(bundle_dir)

    contents_dir.mkdir(parents=True, exist_ok=True)
    (contents_dir / "Info.plist").write_bytes(_render_info_plist(workflow))
    (contents_dir / "document.wflow").write_bytes(_render_document_wflow(workflow))

    click.echo(f"Installed Quick Action: {bundle_dir}")
    click.echo("Next: System Settings → Keyboard → Keyboard Shortcuts → Services/Quick Actions")
    click.echo(f"Assign a shortcut to: {workflow_name}")


@hotkey_group.command("uninstall")
@click.option("--name", default="Voicepipe Toggle", show_default=True)
def hotkey_uninstall(name: str) -> None:
    """Remove the macOS Quick Action workflow bundle."""
    if not is_macos():
        raise click.ClickException("Hotkey installer is only available on macOS.")

    workflow_name = (name or "").strip() or "Voicepipe Toggle"
    workflow = QuickActionWorkflow(name=workflow_name, command="")
    bundle_dir = _workflow_dir(workflow)
    if not bundle_dir.exists():
        click.echo(f"No workflow found at: {bundle_dir}")
        return
    _safe_rmtree(bundle_dir)
    click.echo(f"Removed: {bundle_dir}")

