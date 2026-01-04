$ErrorActionPreference = "Stop"

# Installs a native Windows hotkey runner for Voicepipe (Alt+F5) at login.
# No third-party hotkey tools required.
#
# What it does:
# - Creates a Startup-folder shortcut that runs:
#     pythonw.exe -m voicepipe.win_hotkey
#
# Verify:
# - Log out/in (or reboot), then press Alt+F5.
# - Check `%LOCALAPPDATA%\voicepipe\logs\voicepipe-fast.log`.

$pythonw = (Get-Command pythonw -ErrorAction Stop).Source
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Voicepipe (Alt+F5).lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m voicepipe.win_hotkey"
$shortcut.WorkingDirectory = $env:USERPROFILE
$shortcut.WindowStyle = 7 # minimized (pythonw has no console anyway)
$shortcut.Description = "Voicepipe hotkey runner (Alt+F5)"
$shortcut.Save()

Write-Host "Installed Startup shortcut:"
Write-Host "  $shortcutPath"
Write-Host "Target:"
Write-Host "  $pythonw -m voicepipe.win_hotkey"

