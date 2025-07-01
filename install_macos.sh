#!/bin/bash
#
# Voicepipe macOS Installation Script
# Installs voicepipe, dependencies, and sets up a launchd user agent.
#

set -e # Exit immediately if a command exits with a non-zero status.

echo "Voicepipe macOS Installation Script"
echo "==================================="
echo

# --- Helper Functions ---
ask_yes_no() {
    while true; do
        read -p "$1 [y/N]: " yn
        case $yn in
            [Yy]* ) return 0;;
            [Nn]* | "" ) return 1;;
            * ) echo "Please answer yes or no.";;
        esac
    done
}

# --- Check Homebrew ---
echo "Checking for Homebrew..."
if ! command -v brew &> /dev/null; then
    echo "Homebrew not found."
    if ask_yes_no "Would you like to install Homebrew now? (This will run the official installer script)"; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Attempt to add brew to PATH for the current session
        # For Apple Silicon
        if [ -x "/opt/homebrew/bin/brew" ]; then
            export PATH="/opt/homebrew/bin:$PATH"
        # For Intel Macs
        elif [ -x "/usr/local/bin/brew" ]; then
            export PATH="/usr/local/bin:$PATH"
        fi
         echo "Homebrew installation attempted. Please ensure it's correctly installed and in your PATH."
         echo "You might need to open a new terminal session."
         if ! command -v brew &> /dev/null; then
            echo "ERROR: Homebrew still not found after installation attempt. Please install it manually and re-run this script."
            exit 1
         fi
    else
        echo "Homebrew is required. Please install it from https://brew.sh/ and re-run this script."
        exit 1
    fi
fi
echo "✓ Homebrew found."

# --- Install PortAudio ---
echo -e "\nChecking for PortAudio..."
if brew list portaudio &>/dev/null; then
    echo "✓ PortAudio already installed via Homebrew."
else
    echo "PortAudio not found via Homebrew."
    if ask_yes_no "Would you like to install PortAudio now using 'brew install portaudio'?"; then
        brew install portaudio
    else
        echo "PortAudio is required for audio recording. Please install it (e.g., 'brew install portaudio') and re-run."
        exit 1
    fi
fi
echo "✓ PortAudio requirement addressed."

# --- Install Voicepipe ---
echo -e "\nInstalling Voicepipe..."
INSTALL_METHOD=""
if command -v pipx &> /dev/null; then
    if ask_yes_no "pipx is available. Would you like to install Voicepipe using pipx (recommended for isolated environment)?"; then
        INSTALL_METHOD="pipx"
    fi
fi

if [ -z "$INSTALL_METHOD" ]; then
    if ask_yes_no "Would you like to install Voicepipe using pip in a Python virtual environment? (A '.venv' directory will be created here)"; then
        INSTALL_METHOD="pip_venv"
    else
        echo "Installation cancelled. Voicepipe not installed."
        exit 1
    fi
fi

VOICEPIPE_EXE_PATH=""
PYTHON_VENV_PATH=""

INSTALL_EXTRAS="[systray,typing]" # Include both systray and typing extras

if [ "$INSTALL_METHOD" == "pipx" ]; then
    echo "Installing Voicepipe with systray and typing support using pipx..."
    pipx install "voicepipe$INSTALL_EXTRAS"
    # Ensure pipx path is in current PATH (pipx ensurepath might have already done this for the shell config)
    if ! command -v voicepipe &> /dev/null; then
         export PATH="$HOME/.local/bin:$PATH" # Common pipx path
    fi
    VOICEPIPE_EXE_PATH=$(command -v voicepipe)
    if [ -z "$VOICEPIPE_EXE_PATH" ]; then
        echo "ERROR: Could not find voicepipe executable after pipx installation. Check your PATH."
        echo "You might need to run 'pipx ensurepath' and open a new terminal."
        exit 1
    fi
    echo "✓ Voicepipe installed via pipx. Executable: $VOICEPIPE_EXE_PATH"
else # pip_venv
    echo "Creating Python virtual environment '.venv'..."
    python3 -m venv .venv
    echo "Activating virtual environment..."
    source .venv/bin/activate
    PYTHON_VENV_PATH=$(pwd)/.venv
    echo "Installing Voicepipe with systray and typing support in .venv using pip..."
    pip install "voicepipe$INSTALL_EXTRAS"
    VOICEPIPE_EXE_PATH="$PYTHON_VENV_PATH/bin/voicepipe"
    if [ ! -f "$VOICEPIPE_EXE_PATH" ]; then
        echo "ERROR: Could not find voicepipe executable in .venv after pip installation."
        exit 1
    fi
    echo "✓ Voicepipe installed via pip in .venv. Executable: $VOICEPIPE_EXE_PATH"
    echo "NOTE: To run voicepipe manually from this terminal, ensure the venv is active or use '$PYTHON_VENV_PATH/bin/voicepipe'."
fi


# --- Setup launchd Agent ---
echo -e "\nSetting up launchd User Agent for Voicepipe daemon..."

# Define paths and names
EFFECTIVE_USER=$(whoami)
USER_HOME=$(eval echo ~"$EFFECTIVE_USER") # More robust way to get home directory
PLIST_LABEL="com.$EFFECTIVE_USER.voicepipe.daemon"
PLIST_FILENAME="$PLIST_LABEL.plist"
LAUNCH_AGENTS_DIR="$USER_HOME/Library/LaunchAgents"
TARGET_PLIST_PATH="$LAUNCH_AGENTS_DIR/$PLIST_FILENAME"
TEMPLATE_PLIST_PATH="scripts/macos/voicepipe.daemon.plist.template" # Relative to script execution dir

if [ ! -f "$TEMPLATE_PLIST_PATH" ]; then
    echo "ERROR: launchd plist template not found at $TEMPLATE_PLIST_PATH."
    echo "Please ensure you are running this script from the root of the voicepipe project directory."
    exit 1
fi

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$LAUNCH_AGENTS_DIR"

# Check if plist already exists
if [ -f "$TARGET_PLIST_PATH" ]; then
    echo "WARNING: An existing launchd plist was found at $TARGET_PLIST_PATH."
    if ask_yes_no "Do you want to overwrite it? (This will stop and unload the current service if it's running)"; then
        echo "Unloading existing agent (if loaded)..."
        launchctl unload "$TARGET_PLIST_PATH" 2>/dev/null || true # Ignore error if not loaded
        rm "$TARGET_PLIST_PATH"
    else
        echo "Skipping launchd agent setup. You may need to configure it manually."
        echo "Installation of Voicepipe executable is complete."
        exit 0
    fi
fi

echo "Copying template to $TARGET_PLIST_PATH..."
cp "$TEMPLATE_PLIST_PATH" "$TARGET_PLIST_PATH"

echo "Configuring plist file..."
# Replace placeholders using sed. Using | as delimiter for sed to avoid issues with paths containing /
sed -i '' "s|com.USERNAME.voicepipe.daemon|$PLIST_LABEL|g" "$TARGET_PLIST_PATH"
sed -i '' "s|/FULL/PATH/TO/voicepipe|$VOICEPIPE_EXE_PATH|g" "$TARGET_PLIST_PATH"

# Working directory: recommend user's home for global installs, or project dir if venv here.
# For pipx installs, daemon should ideally not depend on CWD for .env, but use a global config path or expect .env in $HOME.
# Let's set WorkingDirectory to user's home by default. User can change if needed.
DEFAULT_WORKING_DIR="$USER_HOME"
sed -i '' "s|/Users/USERNAME/.config/voicepipe|$DEFAULT_WORKING_DIR/.config/voicepipe|g" "$TARGET_PLIST_PATH" # Default config dir
sed -i '' "s|/Users/USERNAME/Library/Logs/|$USER_HOME/Library/Logs/|g" "$TARGET_PLIST_PATH"
sed -i '' "s|/Users/USERNAME|$USER_HOME|g" "$TARGET_PLIST_PATH" # General USERNAME replacement just in case

# Update PATH in plist: Add the directory of the voicepipe executable.
VOICEPIPE_BIN_DIR=$(dirname "$VOICEPIPE_EXE_PATH")
# A more robust PATH might also include Python's bin dir if not via pipx.
# For pipx, ~/.local/bin is usually enough. For venv, the venv's bin is critical.
# The default PATH in template is basic. Adding specific paths is safer.
# This prepends the found bin dir and the standard user local bin.
LAUNCHD_ENV_PATH="$VOICEPIPE_BIN_DIR:$USER_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
sed -i '' "s|PATH_PLACEHOLDER_SED_REPLACEMENT_STRING|$LAUNCHD_ENV_PATH|g" "$TARGET_PLIST_PATH"
# Fallback for the original simpler PATH string if the complex replacement string wasn't in template
sed -i '' "s|/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin|$LAUNCHD_ENV_PATH|g" "$TARGET_PLIST_PATH"


# Remove or comment out API key line
sed -i '' "s|<string>YOUR_API_KEY_HERE_OR_LEAVE_EMPTY_TO_USE_DOTENV</string>|<!-- API Key should be set via .env file or shell environment -->|g" "$TARGET_PLIST_PATH"

echo "✓ Plist configured at $TARGET_PLIST_PATH"
echo "IMPORTANT: Please review the plist file ($TARGET_PLIST_PATH) for correctness, especially:"
echo "  - The 'ProgramArguments' (path to voicepipe)."
echo "  - 'WorkingDirectory' (currently set to $DEFAULT_WORKING_DIR/.config/voicepipe, ensure .env is locatable from there or in $USER_HOME if daemon checks $HOME)."
echo "  - 'EnvironmentVariables' -> 'PATH'. It has been set to: $LAUNCHD_ENV_PATH"
echo "    If 'voicepipe daemon' fails to start, an incorrect PATH in the plist is a common cause."
echo "    You might need to add the path to the Python interpreter that voicepipe uses if it's not standard."

# --- Load launchd Agent ---
if ask_yes_no "Would you like to load and start the Voicepipe daemon now via launchctl?"; then
    echo "Loading and starting agent..."
    launchctl load "$TARGET_PLIST_PATH"
    # launchctl start "$PLIST_LABEL" # 'start' is often not needed if RunAtLoad is true and it's a new load.
                                  # If it was already loaded and just updated, a kickstart might be needed.
                                  # For simplicity, 'load' usually suffices for user agents on first setup.
    echo "✓ Voicepipe daemon agent loaded. It should start automatically on login."
    echo "To check status: launchctl list | grep $PLIST_LABEL"
    echo "To stop: launchctl unload $TARGET_PLIST_PATH"
    echo "Logs can be found at: $USER_HOME/Library/Logs/VoicepipeDaemon.out.log and .err.log"
else
    echo "Skipped loading agent."
    echo "To load it later: launchctl load $TARGET_PLIST_PATH"
fi

echo -e "\n--- Installation Complete ---"
echo "Voicepipe executable is at: $VOICEPIPE_EXE_PATH"
echo "Launchd agent plist is at: $TARGET_PLIST_PATH"
echo "Remember to configure your OpenAI API Key, e.g., by creating a .env file in $DEFAULT_WORKING_DIR or your project directory."
echo "If you used a venv for installation, activate it with 'source .venv/bin/activate' to use 'voicepipe' directly in this terminal."
echo "Done."
