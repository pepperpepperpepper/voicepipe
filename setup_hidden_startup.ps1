# Setup Voicepipe to run completely hidden on startup

$startupFolder = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupFolder "Voicepipe.lnk"
$targetPath = Join-Path $PWD "voicepipe_startup_truly_hidden.vbs"

# Remove old shortcuts
Write-Host "Removing old startup entries..."
Remove-Item (Join-Path $startupFolder "VoicepipeDaemon.lnk") -ErrorAction SilentlyContinue
Remove-Item (Join-Path $startupFolder "VoicepipeHotkey.lnk") -ErrorAction SilentlyContinue
Remove-Item $shortcutPath -ErrorAction SilentlyContinue

# Create new shortcut
Write-Host "Creating new hidden startup shortcut..."
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = "wscript.exe"
$Shortcut.Arguments = "`"$targetPath`""
$Shortcut.WorkingDirectory = $PWD.Path
$Shortcut.WindowStyle = 7  # Minimized
$Shortcut.Description = "Voicepipe Recording Daemon (Hidden)"
$Shortcut.Save()

Write-Host "Startup configuration complete!"
Write-Host "Voicepipe will now run completely hidden on Windows startup."
Write-Host ""
Write-Host "Created shortcut: $shortcutPath"
Write-Host "Target: $targetPath"