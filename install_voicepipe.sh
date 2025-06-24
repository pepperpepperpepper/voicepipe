#!/bin/bash
#
# Install script for voicepipe
# Supports multiple installation methods

set -e

echo "Voicepipe Installation Script"
echo "============================"
echo

# Detect if we're in the voicepipe directory
if [ ! -f "pyproject.toml" ] || [ ! -d "voicepipe" ]; then
    echo "Error: This script must be run from the voicepipe directory"
    exit 1
fi

# Check for pipx
if command -v pipx &> /dev/null; then
    echo "Found pipx. Installing with pipx (recommended)..."
    pipx uninstall voicepipe 2>/dev/null || true
    pipx install .
    echo
    echo "Installation complete! voicepipe is now available in your PATH."
    echo "Run 'voicepipe --help' to get started."
    
# Check for pip
elif command -v pip &> /dev/null; then
    echo "pipx not found, using pip..."
    echo "Creating virtual environment..."
    
    # Create venv if it doesn't exist
    if [ ! -d "venv" ]; then
        python -m venv venv
    fi
    
    # Activate and install
    source venv/bin/activate
    pip install -e .
    
    echo
    echo "Installation complete!"
    echo "To use voicepipe, first activate the virtual environment:"
    echo "  source venv/bin/activate"
    echo "Then run: voicepipe --help"
    
else
    echo "Error: Neither pipx nor pip found."
    echo "Please install Python and pip first."
    exit 1
fi

echo

# Install systemd service
if command -v systemctl &> /dev/null && [ -f "voicepipe.service.template" ]; then
    echo "Setting up systemd service..."
    
    # Get the voicepipe command path
    VOICEPIPE_CMD=$(which voicepipe)
    
    # Create service file from template
    sed -e "s|VOICEPIPE_COMMAND|$VOICEPIPE_CMD|g" \
        -e "s|HOME_DIR|$HOME|g" \
        voicepipe.service.template > ~/.config/systemd/user/voicepipe.service
    
    # Reload systemd and enable service
    systemctl --user daemon-reload
    systemctl --user enable voicepipe.service
    
    echo "Systemd service installed!"
    echo
    echo "Next steps:"
    echo "1. Create a .env file with your OpenAI API key:"
    echo "   echo 'OPENAI_API_KEY=your-key-here' > .env"
    echo "2. Start the daemon: systemctl --user start voicepipe"
    echo "3. Record audio: voicepipe record"
else
    echo "Next steps:"
    echo "1. Create a .env file with your OpenAI API key:"
    echo "   echo 'OPENAI_API_KEY=your-key-here' > .env"
    echo "2. Start the daemon: voicepipe daemon"
    echo "3. Record audio: voicepipe record"
fi