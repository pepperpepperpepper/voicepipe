#!/usr/bin/env python3
"""
Systray icon toggle script - shows/hides an icon in the system tray
Usage: python3 systray_toggle.py [--hide]
"""

import sys
import threading
import signal
import os
from pathlib import Path

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    print("Error: Required libraries not installed.")
    print("Please install: pip install pystray pillow")
    sys.exit(1)

class SystrayToggle:
    def __init__(self):
        self.icon = None
        self.visible = True
        self.setup_icon()
    
    def create_image(self, color='green'):
        """Create a simple colored circle icon"""
        width = 64
        height = 64
        image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw circle
        if color == 'green':
            fill = (0, 255, 0, 255)
        elif color == 'red':
            fill = (255, 0, 0, 255)
        else:
            fill = (128, 128, 128, 255)
            
        draw.ellipse([8, 8, width-8, height-8], fill=fill)
        return image
    
    def toggle_visibility(self, icon, item):
        """Toggle icon visibility"""
        self.visible = not self.visible
        if self.visible:
            icon.icon = self.create_image('green')
            icon.title = "Status: Active"
        else:
            icon.icon = self.create_image('red')
            icon.title = "Status: Inactive"
    
    def quit_app(self, icon, item):
        """Quit the application"""
        icon.stop()
    
    def setup_icon(self):
        """Setup the system tray icon"""
        # Create menu
        menu = pystray.Menu(
            pystray.MenuItem("Toggle", self.toggle_visibility),
            pystray.MenuItem("Quit", self.quit_app)
        )
        
        # Create icon
        self.icon = pystray.Icon(
            "systray_toggle",
            self.create_image('green'),
            "Status: Active",
            menu
        )
    
    def run(self, hide=False):
        """Run the systray application"""
        if hide:
            self.visible = False
            self.icon.icon = self.create_image('red')
            self.icon.title = "Status: Inactive"
        
        # Handle Ctrl+C gracefully
        def signal_handler(sig, frame):
            self.icon.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        
        # Run icon
        self.icon.run()

def main():
    """Main function"""
    hide = '--hide' in sys.argv
    
    app = SystrayToggle()
    app.run(hide)

if __name__ == "__main__":
    main()