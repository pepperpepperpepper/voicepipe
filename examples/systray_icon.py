#!/usr/bin/env python3
"""Example systray icon script (experimental).

This is not part of the supported Voicepipe CLI surface; it exists as a
reference for pystray usage.
"""

import sys
import signal
import os

try:
    import pystray
    from PIL import Image
except ImportError:
    print("Error: Required libraries not installed.")
    print("Please install: pip install pystray pillow")
    sys.exit(1)

class SystrayIcon:
    def __init__(self, icon_path):
        self.icon_path = icon_path
        self.icon = None
        self.visible = True
        
    def load_icon(self):
        """Load the icon from file"""
        try:
            # Load the TIFF image
            image = Image.open(self.icon_path)
            # Convert to RGBA if needed
            if image.mode != 'RGBA':
                image = image.convert('RGBA')
            # Resize to standard systray size if too large
            if image.width > 64 or image.height > 64:
                image = image.resize((48, 48), Image.Resampling.LANCZOS)
            return image
        except Exception as e:
            print(f"Error loading icon: {e}")
            # Fallback to a simple colored icon
            image = Image.new('RGBA', (48, 48), (0, 0, 0, 0))
            return image
    
    def toggle_visibility(self, icon, item):
        """Toggle icon visibility"""
        self.visible = not self.visible
        if self.visible:
            icon.title = "Mail App - Active"
        else:
            icon.title = "Mail App - Hidden"
    
    def quit_app(self, icon, item):
        """Quit the application"""
        icon.stop()
    
    def run(self):
        """Run the systray application"""
        # Create menu
        menu = pystray.Menu(
            pystray.MenuItem("Toggle", self.toggle_visibility),
            pystray.MenuItem("Quit", self.quit_app)
        )
        
        # Create icon
        self.icon = pystray.Icon(
            "mail_icon",
            self.load_icon(),
            "Mail App",
            menu
        )
        
        # Handle Ctrl+C gracefully
        def signal_handler(sig, frame):
            self.icon.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        
        # Run icon
        print(f"Showing icon: {os.path.basename(self.icon_path)}")
        print("Right-click for menu, Ctrl+C to quit")
        self.icon.run()

def main():
    """Main function"""
    # Use the feh marked Mail.App icon
    icon_path = "/home/pepper/step_icons/feh_012653_000001_Mail.App-vox.tiff"
    
    if not os.path.exists(icon_path):
        print(f"Error: Icon not found at {icon_path}")
        sys.exit(1)
    
    app = SystrayIcon(icon_path)
    app.run()

if __name__ == "__main__":
    main()
