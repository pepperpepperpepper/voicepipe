#!/bin/bash
# Install voicepipe-fast command and set up hotkeys

echo "Installing voicepipe-fast..."

# Copy the fast script to ~/.local/bin (already in PATH)
cp voicepipe-fast /home/pepper/.local/bin/voicepipe-fast
chmod +x /home/pepper/.local/bin/voicepipe-fast

echo "✓ voicepipe-fast installed to ~/.local/bin/"
echo ""
echo "Testing voicepipe-fast..."
if /home/pepper/.local/bin/voicepipe-fast status >/dev/null 2>&1; then
    echo "✓ voicepipe-fast is working!"
else
    echo "⚠ Warning: voicepipe-fast test failed. Is the daemon running?"
    echo "  Start it with: systemctl --user start voicepipe.service"
fi

echo ""
echo "=== Hotkey Setup ==="
echo ""
echo "Add one of these to your window manager config:"
echo ""
echo "For Fluxbox (~/.fluxbox/keys):"
echo "  Mod4 F5 :Exec /home/pepper/.local/bin/voicepipe-fast toggle"
echo ""
echo "For i3 (~/.config/i3/config):"
echo "  bindsym \$mod+F5 exec /home/pepper/.local/bin/voicepipe-fast toggle"
echo ""
echo "For sway (~/.config/sway/config):"
echo "  bindsym \$mod+F5 exec /home/pepper/.local/bin/voicepipe-fast toggle"
echo ""
echo "For XFCE (use xfce4-keyboard-settings):"
echo "  Command: /home/pepper/.local/bin/voicepipe-fast toggle"
echo "  Shortcut: Super+F5"
echo ""
echo "For KDE (use System Settings → Shortcuts):"
echo "  Command: /home/pepper/.local/bin/voicepipe-fast toggle"
echo "  Shortcut: Meta+F5"
echo ""
echo "For GNOME (use Settings → Keyboard → Custom Shortcuts):"
echo "  Name: Voicepipe Toggle"
echo "  Command: /home/pepper/.local/bin/voicepipe-fast toggle"
echo "  Shortcut: Super+F5"
echo ""
echo "For generic X11 (using xbindkeys):"
echo "  Add to ~/.xbindkeysrc:"
echo '  "/home/pepper/.local/bin/voicepipe-fast toggle"'
echo "    Mod4+F5"
echo ""
echo "Note: Mod4 = Super/Windows key"