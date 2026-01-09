<#
.SYNOPSIS
  Install Voicepipe from source on Windows.

.DESCRIPTION
  Installs Voicepipe from the current repo directory into the current user's
  Python environment (via `pip install --user .`).

  Optional:
  - Imports `OPENAI_API_KEY` / `ELEVENLABS_API_KEY` from `$env:USERPROFILE\.api-keys`
    into the canonical Voicepipe env file `%APPDATA%\voicepipe\voicepipe.env`.
  - Installs the native-ish Alt+F5 hotkey runner to start at login.
  - Performs a best-effort cleanup (remove old Scheduled Task + stale lock files)
    to keep reinstalls reliable (use `-SkipCleanup` to disable).

.EXAMPLE
  # Install + import keys (if .api-keys exists) + install hotkey startup shortcut
  .\install.ps1 -Hotkey

.EXAMPLE
  # Use a specific python executable
  .\install.ps1 -Python C:\Python312\python.exe -Hotkey

.EXAMPLE
  # Install hotkey runner elevated (so it can type into admin terminals)
  .\install.ps1 -Hotkey -HotkeyElevated
#>

param(
  [string]$Python = "",
  [switch]$Hotkey,
  [switch]$HotkeyElevated,
  [switch]$SkipApiKeysImport,
  [switch]$SkipCleanup
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
  param([string]$Preferred)

  if ($Preferred) {
    $cmd = Get-Command $Preferred -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    if (Test-Path -LiteralPath $Preferred) { return $Preferred }
    throw "Python not found: $Preferred"
  }

  foreach ($candidate in @("python3.12", "python")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }

  throw (
    "Python not found. Install Python 3.12 (recommended) and Git, then retry.`n" +
    "Example (Chocolatey): choco install -y python312 git"
  )
}

function Require-SupportedPython {
  param([string]$PythonExe)

  $verText = & $PythonExe -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}')" 2>$null
  $ver = $null
  try { $ver = [version]$verText } catch { $ver = $null }
  if (-not $ver) {
    throw "Could not determine Python version (got: $verText)"
  }
  if ($ver.Major -ne 3 -or $ver.Minor -lt 9 -or $ver.Minor -gt 12) {
    throw "Unsupported Python version $verText. Voicepipe currently supports Python 3.9-3.12."
  }
  return $verText
}

function Import-ApiKeysIfPresent {
  param([string]$ApiKeysPath, [string]$EnvFilePath)

  if (-not (Test-Path -LiteralPath $ApiKeysPath)) { return }

  $raw = (Get-Content -Raw -Path $ApiKeysPath -ErrorAction SilentlyContinue)
  if (-not $raw) { return }

  $openai = [regex]::Match($raw, '(?m)^export\s+OPENAI_API_KEY\s*=\s*(.*)$').Groups[1].Value.Trim()
  $eleven = [regex]::Match($raw, '(?m)^export\s+ELEVENLABS_API_KEY\s*=\s*(.*)$').Groups[1].Value.Trim()
  if (-not $eleven) {
    $eleven = [regex]::Match($raw, '(?m)^export\s+XI_API_KEY\s*=\s*(.*)$').Groups[1].Value.Trim()
  }

  if (-not $openai -and -not $eleven) {
    Write-Host "No OPENAI_API_KEY/ELEVENLABS_API_KEY found in $ApiKeysPath"
    return
  }

  $existing = ""
  try {
    if (Test-Path -LiteralPath $EnvFilePath) {
      # BOM-safe read; the string returned will not include a UTF-8 BOM.
      $existing = [System.IO.File]::ReadAllText($EnvFilePath)
    }
  } catch {
    $existing = ""
  }

  $out = $existing.TrimEnd("`r", "`n")
  if ($out) { $out += "`n" }

  if ($openai -and ($existing -notmatch '(?m)^\s*OPENAI_API_KEY\s*=')) {
    $out += "OPENAI_API_KEY=$openai`n"
  }
  if ($eleven -and ($existing -notmatch '(?m)^\s*(ELEVENLABS_API_KEY|XI_API_KEY)\s*=')) {
    $out += "ELEVENLABS_API_KEY=$eleven`n"
  }

  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($EnvFilePath, $out, $utf8NoBom)

  Write-Host "Updated $EnvFilePath (openai=$([bool]$openai) eleven=$([bool]$eleven))"
}

function Cleanup-VoicepipeInstall {
  # Best-effort cleanup to prevent stale hotkey runners / lock files from
  # breaking a reinstall.
  if ($SkipCleanup) { return }

  $taskName = "Voicepipe Toggle"
  try {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
      Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
      Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue | Out-Null
      Write-Host "Removed Scheduled Task: $taskName"
    }
  } catch {
    # Ignore - the Task Scheduler module may be unavailable.
  }

  $startupDir = $null
  try { $startupDir = [Environment]::GetFolderPath("Startup") } catch { $startupDir = $null }
  if (-not $startupDir) {
    try { $startupDir = Join-Path $env:APPDATA "Microsoft\\Windows\\Start Menu\\Programs\\Startup" } catch { $startupDir = $null }
  }
  if ($startupDir) {
    foreach ($name in @(
      "Voicepipe Toggle.lnk",
      "voicepipe-hotkey.ahk",
      "voicepipe-hotkey.ahk.lnk",
      "voicepipe-hotkey.lnk"
    )) {
      try {
        $p = Join-Path $startupDir $name
        Remove-Item -Force -LiteralPath $p -ErrorAction SilentlyContinue
      } catch { }
    }
  }

  foreach ($p in @(
    (Join-Path $env:LOCALAPPDATA "voicepipe\\run\\voicepipe-fast.lock"),
    (Join-Path $env:LOCALAPPDATA "voicepipe\\run\\voicepipe-fast.time"),
    (Join-Path $env:LOCALAPPDATA "voicepipe\\run\\voicepipe-last.txt"),
    (Join-Path $env:TEMP "voicepipe\\voicepipe-fast.lock"),
    (Join-Path $env:TEMP "voicepipe\\voicepipe-fast.time"),
    (Join-Path $env:TEMP "voicepipe\\voicepipe-last.txt")
  )) {
    try { Remove-Item -Force -LiteralPath $p -ErrorAction SilentlyContinue } catch { }
  }
}

if (-not (Test-Path -LiteralPath "pyproject.toml") -or -not (Test-Path -LiteralPath "voicepipe")) {
  throw "Run this from the voicepipe repo root (expected pyproject.toml and voicepipe/)."
}

$py = Resolve-PythonExecutable -Preferred $Python
$verText = Require-SupportedPython -PythonExe $py
Write-Host "Using Python: $py ($verText)"

Cleanup-VoicepipeInstall

Write-Host "Installing Voicepipe ..."
& $py -m pip install -U pip
& $py -m pip uninstall -y voicepipe 2>$null | Out-Null
& $py -m pip install -U .
if ($LASTEXITCODE -ne 0) {
  Write-Host "pip install failed (exit=$LASTEXITCODE). Retrying with --user ..."
  & $py -m pip install --user -U .
  if ($LASTEXITCODE -ne 0) {
    throw "pip install failed (exit=$LASTEXITCODE)"
  }
}

if (-not $SkipApiKeysImport) {
  $apiKeysPath = Join-Path $env:USERPROFILE ".api-keys"
  $envDir = Join-Path $env:APPDATA "voicepipe"
  $envPath = Join-Path $envDir "voicepipe.env"
  New-Item -ItemType Directory -Force -Path $envDir | Out-Null
  Import-ApiKeysIfPresent -ApiKeysPath $apiKeysPath -EnvFilePath $envPath
}

if ($Hotkey) {
  Write-Host "Installing hotkey helper (Alt+F5) ..."
  if ($HotkeyElevated) {
    & $py -m voicepipe.cli hotkey install --force --elevated
  } else {
    & $py -m voicepipe.cli hotkey install --force
  }
}

Write-Host ""
Write-Host "Done."
Write-Host "Try:"
Write-Host "  voicepipe doctor env"
Write-Host "  voicepipe-fast toggle"
Write-Host ""
Write-Host "Windows hotkey log:"
Write-Host "  %LOCALAPPDATA%\\voicepipe\\logs\\voicepipe-fast.log"
