#Requires -Version 5.1

<#
.SYNOPSIS
    Installation script for Voicepipe on Windows.
.DESCRIPTION
    This script checks for dependencies (Python, Poetry) and installs
    the Voicepipe application and its requirements.
.NOTES
    Author: Jules (AI Assistant)
    Last Modified: $(Get-Date)
#>

Write-Host "Voicepipe Windows Installation Script"
Write-Host "==================================="
Write-Host

# --- Configuration ---
$ProjectFiles = @("pyproject.toml", "voicepipe")
$PoetryInstallCommand = 'poetry install --extras "systray windows-support"' # Assuming windows-support extra for pywin32

# --- Helper Functions ---
function Test-CommandExists {
    param (
        [string]$CommandName
    )
    return (Get-Command $CommandName -ErrorAction SilentlyContinue) -ne $null
}

function Test-PythonInstalled {
    if (-not (Test-CommandExists "python")) {
        Write-Error "Python does not seem to be installed or is not in your PATH."
        Write-Host "Please install Python 3.9+ from https://www.python.org/downloads/windows/"
        Write-Host "Ensure you check 'Add Python to PATH' during installation."
        return $false
    }
    # Consider adding a version check here if necessary
    # (python --version) ...
    Write-Host "✓ Python found." -ForegroundColor Green
    return $true
}

function Test-PoetryInstalled {
    if (-not (Test-CommandExists "poetry")) {
        Write-Warning "Poetry does not seem to be installed or is not in your PATH."
        Write-Host "Poetry is used to manage project dependencies and installation."
        Write-Host "You can install it by following the instructions at: https://python-poetry.org/docs/#installation"
        Write-Host "Typically, this involves running: (Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | py"

        $choice = Read-Host "Would you like to attempt to install Poetry now using pip? (pip install poetry) [y/N]"
        if ($choice -eq 'y') {
            try {
                Write-Host "Attempting to install Poetry using pip..."
                pip install poetry
                if (-not (Test-CommandExists "poetry")) {
                     Write-Warning "Poetry installation via pip attempted, but 'poetry' command is still not found."
                     Write-Host "You might need to ensure the Python scripts directory is in your PATH."
                     Write-Host "Default user script path: %APPDATA%\Python\PythonXX\Scripts"
                     Write-Host "Default system script path: C:\Program Files\PythonXX\Scripts"
                     return $false
                }
                Write-Host "✓ Poetry installed via pip successfully." -ForegroundColor Green
            } catch {
                Write-Error "Failed to install Poetry using pip: $($_.Exception.Message)"
                return $false
            }
        } else {
            return $false
        }
    }
    Write-Host "✓ Poetry found." -ForegroundColor Green
    return $true
}

function Test-InProjectDirectory {
    $missingFiles = @()
    foreach ($fileOrDir in $ProjectFiles) {
        if (-not (Test-Path $fileOrDir)) {
            $missingFiles += $fileOrDir
        }
    }
    if ($missingFiles.Count -gt 0) {
        Write-Error "This script must be run from the Voicepipe project root directory."
        Write-Host "The following files/directories were not found: $($missingFiles -join ', ')"
        return $false
    }
    Write-Host "✓ Running in project directory." -ForegroundColor Green
    return $true
}

# --- Main Script ---

# 1. Check if running in project directory
if (-not (Test-InProjectDirectory)) {
    exit 1
}

# 2. Check for Python
if (-not (Test-PythonInstalled)) {
    exit 1
}

# 3. Check for Poetry
if (-not (Test-PoetryInstalled)) {
    exit 1
}

# 4. Install Voicepipe
Write-Host "`nAttempting to install Voicepipe using Poetry..."
Write-Host "This will install core dependencies, plus extras for systray (`pystray`, `Pillow`),"
Write-Host "Windows IPC (`pywin32`), and Windows typing (`pyautogui`)."
try {
    Invoke-Expression -Command $PoetryInstallCommand
    Write-Host "✓ Voicepipe installation command executed." -ForegroundColor Green
    Write-Host "If there were no errors above, Voicepipe and its optional Windows features should be installed."
} catch {
    Write-Error "An error occurred during Voicepipe installation: $($_.Exception.Message)"
    Write-Host "Please check the output above for details."
    exit 1
}

Write-Host "`n--- Installation Summary ---" -ForegroundColor Cyan
Write-Host "• Voicepipe and dependencies should now be installed in a virtual environment managed by Poetry."
Write-Host "• To run Voicepipe commands, you'll typically use 'poetry run voicepipe <command>' from this directory."
Write-Host "  Example: poetry run voicepipe --help"
Write-Host "• Audio Input (PyAudio): This project uses PyAudio for recording. On Windows, pip usually installs"
Write-Host "  a version with necessary PortAudio components. If you encounter audio input issues,"
Write-Host "  ensure your microphone is properly configured in Windows and that no other application"
Write-Host "  is exclusively using it. Advanced users needing different audio APIs (like ASIO)"
Write-Host "  might need to compile PyAudio and PortAudio manually."


Write-Host "`n--- Configuration ---" -ForegroundColor Cyan
Write-Host "Remember to set your OpenAI API key."
Write-Host "You can do this by:"
Write-Host "1. Setting an environment variable:"
Write-Host "   `$env:OPENAI_API_KEY='your-api-key-here'` (for the current session)"
Write-Host "   For persistent storage, search for 'Edit the system environment variables'."
Write-Host "2. Creating a '.env' file in the project root with the line:"
Write-Host "   OPENAI_API_KEY=your-api-key-here"

Write-Host "`n--- Running the Daemon (Background Service) ---" -ForegroundColor Cyan
Write-Host "To run the Voicepipe daemon (for background recording and systray):"
Write-Host "1. From this project directory, you can manually start it using Poetry:"
Write-Host "   `poetry run voicepipe daemon`"
Write-Host "2. For it to run automatically or more like a service (without a persistent console window):"
Write-Host "   - Simple background task (no auto-restart, runs as user): Use 'pythonw.exe'."
Write-Host "     You first need to find the path to pythonw.exe within Poetry's virtual environment."
Write-Host "     You can find the venv path by running: `poetry env info --path`"
Write-Host "     Then, the command would be something like: '<path-to-poetry-venv>\Scripts\pythonw.exe voicepipe\daemon.py'"
Write-Host "     You could create a shortcut to this command and place it in your Startup folder for auto-start on login."
Write-Host "       (Shell:startup to open Startup folder)"
Write-Host
Write-Host "   - Robust Windows Service (auto-start, restart on failure, runs as LocalSystem or specified user): Use NSSM."
Write-Host "     NSSM (Non-Sucking Service Manager) is a free tool to run any application as a service."
Write-Host "     Download NSSM from: https://nssm.cc/download"
Write-Host "     Setup Steps (requires Administrator privileges):"
Write-Host "     a. Extract nssm.exe to a memorable location (e.g., C:\NSSM)."
Write-Host "     b. Open an Administrator PowerShell or Command Prompt."
Write-Host "     c. Navigate to the directory where you placed nssm.exe."
Write-Host "     d. Run: `.\nssm.exe install VoicepipeDaemon`"
Write-Host "     e. In the NSSM GUI:"
Write-Host "        - Application Tab -> Path: Select the 'python.exe' (or 'pythonw.exe' for no console) from Poetry's virtual environment."
Write-Host "          (Find venv path with `poetry env info --path`, then it's usually `Scripts\python.exe` inside that)"
Write-Host "        - Application Tab -> Startup directory: Set to the Voicepipe project root directory (this directory)."
Write-Host "        - Application Tab -> Arguments: `voicepipe\daemon.py` (if using python.exe/pythonw.exe directly)"
Write-Host "                                OR if you want to use poetry to run it (ensures full poetry env):"
Write-Host "                                Path: `poetry.exe` (if poetry is in system PATH)"
Write-Host "                                Startup directory: Voicepipe project root"
Write-Host "                                Arguments: `run voicepipe daemon`"
Write-Host "        - Details Tab -> Display name: Voicepipe Recording Daemon"
Write-Host "        - I/O Tab (Optional): Configure output/error logging if needed."
Write-Host "        - Restart Tab (Optional): Configure restart options on failure."
Write-Host "     f. Click 'Install service'."
Write-Host "     g. You can then start the service: `net start VoicepipeDaemon` or via services.msc."
Write-Host "     To remove the service later: `.\nssm.exe remove VoicepipeDaemon` (as Administrator)"

Write-Host "`nInstallation script finished." -ForegroundColor Green
Write-Host "Please report any issues on the project's GitHub page."
