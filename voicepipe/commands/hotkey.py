"""`voicepipe hotkey …` helpers.

On macOS, the most native way to bind a global keyboard shortcut without third
party hotkey apps is a Quick Action (Automator service). This module provides
an opt-in helper to install such a workflow bundle.

On Windows, Voicepipe ships a small stdlib-only hotkey runner
(`pythonw -m voicepipe.win_hotkey`) that registers a global hotkey (Alt+F5 by
default). This module can install a Startup-folder shortcut to run it at login.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import click

from voicepipe.platform import is_macos, is_windows


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
    """Hotkey helpers (macOS Quick Action, Windows Alt+F5)."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def _windows_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
        )
    return (
        Path.home()
        / "AppData"
        / "Roaming"
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Startup"
    )


def _windows_shortcut_path(name: str) -> Path:
    safe = (name or "").strip() or "Voicepipe Toggle"
    if not safe.lower().endswith(".lnk"):
        safe += ".lnk"
    return _windows_startup_dir() / safe


def _windows_task_name(name: str) -> str:
    safe = (name or "").strip() or "Voicepipe Toggle"
    return safe[:-4] if safe.lower().endswith(".lnk") else safe


def _windows_pythonw(python_path: str | None) -> str:
    raw = (python_path or sys.executable).strip() or sys.executable
    try:
        python = Path(raw)
    except Exception:
        return raw
    if python.name.lower() == "pythonw.exe":
        return str(python)
    candidate = python.with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else str(python)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _windows_install_shortcut(*, name: str, python_path: str | None, force: bool) -> Path:
    shortcut_path = _windows_shortcut_path(name)
    if shortcut_path.exists() and not force:
        raise click.ClickException(
            f"Startup shortcut already exists: {shortcut_path} (use --force to overwrite)"
        )

    pythonw = _windows_pythonw(python_path)
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)

    # Use PowerShell to create a real .lnk file (no third-party deps).
    ps = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$pythonw = {_ps_quote(pythonw)}",
            f"$shortcutPath = {_ps_quote(str(shortcut_path))}",
            "$shell = New-Object -ComObject WScript.Shell",
            "$shortcut = $shell.CreateShortcut($shortcutPath)",
            "$shortcut.TargetPath = $pythonw",
            "$shortcut.Arguments = '-m voicepipe.win_hotkey'",
            "$shortcut.WorkingDirectory = $env:USERPROFILE",
            "$shortcut.WindowStyle = 7",
            "$shortcut.Description = 'Voicepipe hotkey runner (Alt+F5)'",
            "$shortcut.Save()",
        ]
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise click.ClickException("powershell not found (required for Windows shortcut install)") from e
    except subprocess.CalledProcessError as e:
        detail = ((e.stderr or "") + "\n" + (e.stdout or "")).strip()
        raise click.ClickException(f"Failed to create Startup shortcut: {detail}") from e

    return shortcut_path


def _windows_install_task(*, name: str, python_path: str | None, force: bool) -> str:
    """Install a Scheduled Task to start the hotkey runner at logon.

    This is generally more reliable than a Startup-folder shortcut in managed /
    locked-down environments.
    """
    task_name = _windows_task_name(name)
    pythonw = _windows_pythonw(python_path)

    ps = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$taskName = {_ps_quote(task_name)}",
            f"$pythonw = {_ps_quote(pythonw)}",
            "$userId = \"$env:USERDOMAIN\\$env:USERNAME\"",
            "if (-not $userId -or $userId -eq '\\\\') { $userId = $env:USERNAME }",
            "$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue",
            "if ($existing -and -not " + ("$true" if force else "$false") + ") {",
            "  throw \"Scheduled Task already exists: $taskName (use --force to overwrite)\"",
            "}",
            "$action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m voicepipe.win_hotkey' -WorkingDirectory $env:USERPROFILE",
            "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId",
            "$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType InteractiveToken -RunLevel LeastPrivilege",
            "Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null",
            # Start now so Alt+F5 works immediately.
            "Start-ScheduledTask -TaskName $taskName | Out-Null",
        ]
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as e:
        raise click.ClickException("powershell not found (required for Windows task install)") from e
    except subprocess.CalledProcessError as e:
        detail = ((e.stderr or "") + "\n" + (e.stdout or "")).strip()
        raise click.ClickException(f"Failed to create Scheduled Task: {detail}") from e

    return task_name


@hotkey_group.command("install")
@click.option("--name", default="Voicepipe Toggle", show_default=True)
@click.option(
    "--python",
    "python_path",
    default=None,
    help="Python executable to use (default: current interpreter)",
)
@click.option(
    "--method",
    type=click.Choice(["task", "startup"], case_sensitive=False),
    default="task",
    show_default=True,
    help="Install via Scheduled Task (recommended) or Startup folder shortcut.",
)
@click.option("--force", is_flag=True, help="Overwrite existing workflow if present")
def hotkey_install(name: str, python_path: str | None, method: str, force: bool) -> None:
    """Install a hotkey helper (macOS Quick Action or Windows Alt+F5)."""
    if is_windows():
        if method.lower() == "startup":
            shortcut_path = _windows_install_shortcut(
                name=name, python_path=python_path, force=force
            )
            click.echo(f"Installed Startup shortcut: {shortcut_path}")
            click.echo("Next: log out/in (or reboot), then press Alt+F5.")
            click.echo("Log: %LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log")
            return

        task_name = _windows_install_task(name=name, python_path=python_path, force=force)
        click.echo(f"Installed Scheduled Task: {task_name}")
        click.echo("Started it (Alt+F5 should work immediately).")
        click.echo("Log: %LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log")
        return

    if not is_macos():
        raise click.ClickException("Hotkey installer is only available on macOS and Windows.")

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
    """Remove the installed hotkey helper."""
    if is_windows():
        task_name = _windows_task_name(name)
        ps = "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$taskName = {_ps_quote(task_name)}",
                "$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue",
                "if ($task) {",
                "  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false | Out-Null",
                "  Write-Output \"Removed Scheduled Task: $taskName\"",
                "} else {",
                "  Write-Output \"No Scheduled Task found: $taskName\"",
                "}",
            ]
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            # Best-effort; continue to shortcut cleanup.
            pass

        shortcut_path = _windows_shortcut_path(name)
        try:
            shortcut_path.unlink()
            click.echo(f"Removed Startup shortcut: {shortcut_path}")
        except FileNotFoundError:
            click.echo(f"No Startup shortcut found at: {shortcut_path}")
        except Exception as e:
            raise click.ClickException(f"Failed to remove {shortcut_path}: {e}") from e
        return

    if not is_macos():
        raise click.ClickException("Hotkey installer is only available on macOS and Windows.")

    workflow_name = (name or "").strip() or "Voicepipe Toggle"
    workflow = QuickActionWorkflow(name=workflow_name, command="")
    bundle_dir = _workflow_dir(workflow)
    if not bundle_dir.exists():
        click.echo(f"No workflow found at: {bundle_dir}")
        return
    _safe_rmtree(bundle_dir)
    click.echo(f"Removed: {bundle_dir}")
