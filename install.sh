#!/bin/bash
# Installation script for voicepipe with systemd user service

set -e

echo "Installing voicepipe..."

# Install the Python package
pip install --user -e .

# Create systemd user directory if it doesn't exist
mkdir -p ~/.config/systemd/user/

# Copy the service file
cp voicepipe.service ~/.config/systemd/user/

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