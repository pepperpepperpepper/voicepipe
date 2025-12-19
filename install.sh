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

VOICEPIPE_FAST_CMD="$VENV_PATH/bin/voicepipe-fast"
if [ -x "$VOICEPIPE_FAST_CMD" ]; then
    ln -sf "$VOICEPIPE_FAST_CMD" "$HOME/.local/bin/voicepipe-fast"
    echo "✓ voicepipe-fast symlinked to ~/.local/bin/voicepipe-fast"
fi

VOICEPIPE_TRANSCRIBE_FILE_CMD="$VENV_PATH/bin/voicepipe-transcribe-file"
if [ -x "$VOICEPIPE_TRANSCRIBE_FILE_CMD" ]; then
    ln -sf "$VOICEPIPE_TRANSCRIBE_FILE_CMD" "$HOME/.local/bin/voicepipe-transcribe-file"
    echo "✓ voicepipe-transcribe-file symlinked to ~/.local/bin/voicepipe-transcribe-file"
fi

VOICEPIPE_TRANSCRIBER_DAEMON_CMD="$VENV_PATH/bin/voicepipe-transcriber-daemon"
if [ -x "$VOICEPIPE_TRANSCRIBER_DAEMON_CMD" ]; then
    ln -sf "$VOICEPIPE_TRANSCRIBER_DAEMON_CMD" "$HOME/.local/bin/voicepipe-transcriber-daemon"
    echo "✓ voicepipe-transcriber-daemon symlinked to ~/.local/bin/voicepipe-transcriber-daemon"
fi

# Ensure a systemd-friendly env file exists for config/secrets.
VOICEPIPE_CONFIG_DIR="$HOME/.config/voicepipe"
VOICEPIPE_ENV_FILE="$VOICEPIPE_CONFIG_DIR/voicepipe.env"
mkdir -p "$VOICEPIPE_CONFIG_DIR"
chmod 700 "$VOICEPIPE_CONFIG_DIR" 2>/dev/null || true
if [ ! -f "$VOICEPIPE_ENV_FILE" ]; then
    if [ -n "$OPENAI_API_KEY" ]; then
        printf 'OPENAI_API_KEY=%s\n' "$OPENAI_API_KEY" > "$VOICEPIPE_ENV_FILE"
    else
        cat > "$VOICEPIPE_ENV_FILE" << EOF
# Voicepipe environment config (used by systemd services and the CLI)
# OPENAI_API_KEY=sk-...
# VOICEPIPE_DEVICE=12
# VOICEPIPE_TRANSCRIBE_MODEL=gpt-4o-transcribe
EOF
    fi
    chmod 600 "$VOICEPIPE_ENV_FILE" 2>/dev/null || true
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
        TRANSCRIBER_COMMAND="$VENV_PATH/bin/voicepipe-transcriber-daemon"
        sed -e "s|{{TRANSCRIBER_COMMAND}}|$TRANSCRIBER_COMMAND|g" \
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
echo "Set your OpenAI API key here (works for systemd services and CLI):"
echo "  $VOICEPIPE_ENV_FILE"
echo "Example:"
echo "  echo 'OPENAI_API_KEY=sk-...' >> $VOICEPIPE_ENV_FILE"
echo
echo "Then restart the transcriber service:"
echo "  systemctl --user restart voicepipe-transcriber.service"
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
