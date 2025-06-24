#!/bin/bash
# Installation script for voicepipe with systemd user service

set -e

echo "Installing voicepipe..."

# Check if we're in the project directory
if [ ! -f "setup.py" ]; then
    echo "Error: setup.py not found. Please run this script from the voicepipe project directory."
    exit 1
fi

# Determine installation method
if command -v pipx &> /dev/null; then
    echo "Using pipx for installation (recommended)..."
    # For development, use pipx install with --editable
    pipx install --editable .
elif command -v pip &> /dev/null; then
    echo "Using pip for installation..."
    # Install with --user flag for local installation
    pip install --user -e .
else
    echo "Error: Neither pipx nor pip found. Please install Python and pip first."
    exit 1
fi

# Wait a moment for the installation to complete
sleep 2

# Find the voicepipe command location
VOICEPIPE_CMD=$(which voicepipe 2>/dev/null || echo "")
if [ -z "$VOICEPIPE_CMD" ]; then
    # Try common locations
    if [ -f "$HOME/.local/bin/voicepipe" ]; then
        VOICEPIPE_CMD="$HOME/.local/bin/voicepipe"
    elif [ -f "$HOME/.local/pipx/venvs/voicepipe/bin/voicepipe" ]; then
        VOICEPIPE_CMD="$HOME/.local/pipx/venvs/voicepipe/bin/voicepipe"
    else
        echo "Error: Could not find voicepipe command after installation"
        exit 1
    fi
fi

echo "Found voicepipe at: $VOICEPIPE_CMD"

# Create systemd user directory if it doesn't exist
mkdir -p ~/.config/systemd/user/

# Generate the service file from template
if [ -f "voicepipe.service.template" ]; then
    sed -e "s|VOICEPIPE_COMMAND|$VOICEPIPE_CMD|g" \
        -e "s|HOME_DIR|$HOME|g" \
        voicepipe.service.template > ~/.config/systemd/user/voicepipe.service
else
    # Fallback: create service file directly
    cat > ~/.config/systemd/user/voicepipe.service << EOF
[Unit]
Description=Voicepipe Recording Service
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

# Reload systemd user daemon
systemctl --user daemon-reload

echo "Installation complete!"
echo ""
echo "To enable and start the voicepipe service:"
echo "  systemctl --user enable voicepipe.service"
echo "  systemctl --user start voicepipe.service"
echo ""
echo "To check service status:"
echo "  systemctl --user status voicepipe.service"
echo ""
echo "The service will:"
echo "- Start automatically when you log in (if enabled)"
echo "- Provide better performance than subprocess mode"
echo "- Handle multiple recording requests efficiently"
echo ""
echo "The CLI will automatically use the service if it's running,"
echo "or fall back to subprocess mode if it's not."