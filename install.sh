#!/bin/bash
#
# Voicepipe Installation Script
# Installs voicepipe using pipx for isolated Python environment
#

set -e

echo "Voicepipe Installation Script"
echo "============================"
echo

# Check if we're in the project directory
if [ ! -f "pyproject.toml" ] || [ ! -d "voicepipe" ]; then
    echo "Error: This script must be run from the voicepipe project directory"
    echo "Make sure pyproject.toml and voicepipe/ directory exist"
    exit 1
fi

# Check for poetry
if ! command -v poetry &> /dev/null; then
    echo "Error: poetry is required but not installed."
    echo
    echo "Please install poetry first:"
    echo "  curl -sSL https://install.python-poetry.org | python3 -"
    echo
    echo "Then ensure poetry is in your PATH."
    exit 1
fi

echo "Installing with poetry..."

# Set in-project venv
export POETRY_VIRTUALENVS_IN_PROJECT=true

# Remove existing venv if any
rm -rf .venv

# Install with systray extra
poetry install --extras systray

echo "✓ Installation complete!"

# Install voicepipe-fast script
    echo "Installing voicepipe-fast control script..."
    if [ -f "voicepipe-fast" ]; then
        cp voicepipe-fast "$HOME/.local/bin/voicepipe-fast"
        chmod +x "$HOME/.local/bin/voicepipe-fast"
        echo "✓ voicepipe-fast installed to ~/.local/bin/"
    else
        echo "Warning: voicepipe-fast script not found in current directory"
    fi
    
    echo "Installing voicepipe-transcribe-file script for file transcription..."
    if [ -f "voicepipe-transcribe-file" ]; then
        cp voicepipe-transcribe-file "$HOME/.local/bin/voicepipe-transcribe-file"
        chmod +x "$HOME/.local/bin/voicepipe-transcribe-file"
        echo "✓ voicepipe-transcribe-file installed to ~/.local/bin/"
    else
        echo "Warning: voicepipe-transcribe-file script not found in current directory"
    fi
echo

# Get venv path from poetry
VENV_PATH=$(poetry env info --path)
if [ -z "$VENV_PATH" ]; then
    echo "Error: Could not find poetry virtual environment"
    exit 1
fi
VOICEPIPE_CMD="$VENV_PATH/bin/voicepipe"

# Symlink to global bin
mkdir -p "$HOME/.local/bin"
ln -sf "$VOICEPIPE_CMD" "$HOME/.local/bin/voicepipe"
echo "✓ voicepipe command symlinked to ~/.local/bin/voicepipe"
echo "Found voicepipe at: $VOICEPIPE_CMD"

# Setup systemd service
if command -v systemctl &> /dev/null; then
    echo "Setting up systemd user service..."
    
    # Create systemd user directory
    mkdir -p ~/.config/systemd/user/
    
    # Generate service file
    if [ -f "voicepipe-recorder.service.template" ]; then
        sed -e "s|VOICEPIPE_COMMAND|$VOICEPIPE_CMD|g" \
            -e "s|HOME_DIR|$HOME|g" \
            voicepipe-recorder.service.template > ~/.config/systemd/user/voicepipe-recorder.service
    else
        # Create service file directly
        cat > ~/.config/systemd/user/voicepipe-recorder.service << EOF
[Unit]
Description=Voicepipe Recorder Service
After=graphical-session.target

[Service]
Type=simple
ExecStart=$VOICEPIPE_CMD daemon
Restart=on-failure
RestartSec=5
Environment="PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin"

# Security hardening
PrivateTmp=no
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/tmp %t
NoNewPrivileges=true

[Install]
WantedBy=default.target
EOF
    fi
    
    # Reload systemd
    systemctl --user daemon-reload
    
    # Install transcriber service from template
    if [ -f "voicepipe-transcriber.service.template" ]; then
        PYTHON_PATH="$VENV_PATH/bin/python"
        SCRIPT_PATH="$(pwd)/transcriber_daemon.py"
        sed -e "s|{{PYTHON_PATH}}|$PYTHON_PATH|g" \
            -e "s|{{SCRIPT_PATH}}|$SCRIPT_PATH|g" \
            voicepipe-transcriber.service.template > ~/.config/systemd/user/voicepipe-transcriber.service
    fi
    
    
    
    # Reload systemd
    systemctl --user daemon-reload
    
    echo "✓ Systemd services configured"
    echo
    echo "Services installed:"
    echo "  • voicepipe-recorder.service - Fast recording daemon"
    echo "  • voicepipe-transcriber.service - Persistent transcription daemon"
    echo
    echo "To enable and start both services:"
    echo "  systemctl --user enable voicepipe-recorder.service voicepipe-transcriber.service"
    echo "  systemctl --user start voicepipe-recorder.service voicepipe-transcriber.service"
    echo
    echo "To check service status:"
    echo "  systemctl --user status voicepipe-recorder.service voicepipe-transcriber.service"
    echo
    echo "To restart services after API key changes:"
    echo "  systemctl --user restart voicepipe-transcriber.service"
fi

echo
echo "Installation Summary:"
echo "===================="
echo "• Voicepipe CLI tool installed with systray support"
echo "• Systemd user service configured (if available)"
echo "• Command: $VOICEPIPE_CMD"
echo "• voicepipe-fast installed to ~/.local/bin/"
echo "• voicepipe-transcribe-file installed to ~/.local/bin/"
echo
echo "Usage:"
echo "  $VOICEPIPE_CMD --help          # Show help"
echo "  $VOICEPIPE_CMD start           # Start recording"
echo "  $VOICEPIPE_CMD stop            # Stop and transcribe"
echo "  $VOICEPIPE_CMD status          # Check status"
echo "  ~/.local/bin/voicepipe-transcribe-file <audio-file>  # Transcribe audio file"
echo
echo "The service provides:"
echo "• Fast recording startup (daemon mode)"
echo "• Systray icon during recording"
echo "• Automatic transcription with OpenAI"
echo
echo "Remember to set your OpenAI API key in your shell configuration:"
echo "  Add 'export OPENAI_API_KEY=your-api-key-here' to ~/.bashrc or ~/.api-keys"
echo "  Then restart the voicepipe service: systemctl --user restart voicepipe-transcriber.service"
echo
echo "Hotkey Setup:"
echo "============="
echo "voicepipe-fast provides minimal-latency recording control."
echo "Add to your window manager config:"
echo
echo "For Fluxbox (~/.fluxbox/keys):"
echo "  Mod1 F5 :Exec $HOME/.local/bin/voicepipe-fast toggle"
echo
echo "For other window managers, bind '$HOME/.local/bin/voicepipe-fast toggle'"
echo "to your preferred hotkey (e.g., Alt+F5, Super+V, etc.)"
