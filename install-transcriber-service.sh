#!/bin/bash
# Install the transcriber service

# Copy service file to systemd user directory
cp /home/pepper/.local/src/voicepipe/voicepipe-transcriber.service ~/.config/systemd/user/

# Reload systemd
systemctl --user daemon-reload

# Enable and start the service
systemctl --user enable voicepipe-transcriber.service
systemctl --user start voicepipe-transcriber.service

# Check status
systemctl --user status voicepipe-transcriber.service

echo "Transcriber service installed and started"
echo "Both services will start automatically on login"