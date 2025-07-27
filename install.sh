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

# Check for pipx
if ! command -v pipx &> /dev/null; then
    echo "Error: pipx is required but not installed."
    echo
    echo "Please install pipx first:"
    echo "  # On Ubuntu/Debian:"
    echo "  sudo apt install pipx"
    echo "  # On Arch Linux:"
    echo "  sudo pacman -S python-pipx"
    echo "  # Or with pip:"
    echo "  pip install --user pipx"
    echo
    echo "Then ensure pipx is in your PATH:"
    echo "  pipx ensurepath"
    exit 1
fi

echo "Installing with pipx..."

# Uninstall any existing version
pipx uninstall voicepipe 2>/dev/null || true

# Install with systray support
pipx install --editable ".[systray]"

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

# Find voicepipe command location
VOICEPIPE_CMD=$(which voicepipe 2>/dev/null || echo "")
if [ -z "$VOICEPIPE_CMD" ]; then
    # Try common locations
    for path in "$HOME/.local/bin/voicepipe" "$HOME/.local/pipx/venvs/voicepipe/bin/voicepipe"; do
        if [ -f "$path" ]; then
            VOICEPIPE_CMD="$path"
            break
        fi
    done
fi

if [ -z "$VOICEPIPE_CMD" ]; then
    echo "Warning: Could not find voicepipe command after installation"
    echo "You may need to add ~/.local/bin to your PATH"
    VOICEPIPE_CMD="voicepipe"
else
    echo "Found voicepipe at: $VOICEPIPE_CMD"
fi

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
        PYTHON_PATH="/home/pepper/.local/share/pipx/venvs/voicepipe/bin/python"
        SCRIPT_PATH="/home/pepper/.local/src/voicepipe/transcriber_daemon.py"
        sed -e "s|{{PYTHON_PATH}}|$PYTHON_PATH|g" \
            -e "s|{{SCRIPT_PATH}}|$SCRIPT_PATH|g" \
            voicepipe-transcriber.service.template > ~/.config/systemd/user/voicepipe-transcriber.service
    fi
    
    # Reload systemd
    systemctl --user daemon-reload
    
    echo "✓ Systemd services configured"
    echo
    echo "To enable and start the services:"
    echo "  systemctl --user enable voicepipe-recorder.service voicepipe-transcriber.service"
    echo "  systemctl --user start voicepipe-recorder.service voicepipe-transcriber.service"
    echo
    echo "To check service status:"
    echo "  systemctl --user status voicepipe-recorder.service voicepipe-transcriber.service"
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
echo "Remember to set your OpenAI API key:"
echo "  export OPENAI_API_KEY='your-api-key-here'"
echo "  # or create a .env file with OPENAI_API_KEY=your-api-key-here"
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
