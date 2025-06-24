#!/usr/bin/env python3
"""
Simple systray icon - shows the feh marked icon in system tray
"""

import sys
import signal
import os
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AppIndicator3', '0.1')
from gi.repository import Gtk, AppIndicator3, GdkPixbuf

class SystrayIcon:
    def __init__(self, icon_path):
        self.icon_path = icon_path
        
        # Create the indicator
        self.indicator = AppIndicator3.Indicator.new(
            "mail-icon",
            icon_path,
            AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )
        
        self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Mail App")
        
        # Create menu
        self.menu = Gtk.Menu()
        
        # Toggle item
        toggle_item = Gtk.MenuItem(label="Toggle")
        toggle_item.connect("activate", self.toggle)
        self.menu.append(toggle_item)
        
        # Separator
        self.menu.append(Gtk.SeparatorMenuItem())
        
        # Quit item
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.quit)
        self.menu.append(quit_item)
        
        self.menu.show_all()
        self.indicator.set_menu(self.menu)
        
    def toggle(self, widget):
        """Toggle visibility"""
        status = self.indicator.get_status()
        if status == AppIndicator3.IndicatorStatus.ACTIVE:
            print("Hiding icon")
            self.indicator.set_status(AppIndicator3.IndicatorStatus.PASSIVE)
        else:
            print("Showing icon")
            self.indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    
    def quit(self, widget):
        """Quit the application"""
        Gtk.main_quit()
    
    def run(self):
        """Run the application"""
        # Handle Ctrl+C
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        
        print(f"Showing icon: {os.path.basename(self.icon_path)}")
        print("Right-click for menu, Ctrl+C to quit")
        
        Gtk.main()

def main():
    """Main function"""
    # Convert TIFF to PNG for better compatibility
    # Icon is now bundled with voicepipe project
    icon_tiff = "/home/pepper/.local/share/voicepipe/voicepipe/assets/recording_icon.tiff"
    icon_png = "/tmp/mail_icon.png"
    
    if os.path.exists(icon_tiff):
        try:
            # Load and save as PNG
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(icon_tiff)
            pixbuf.savev(icon_png, "png", [], [])
            icon_path = icon_png
        except Exception as e:
            print(f"Error converting icon: {e}")
            # Use the XPM version if available
            icon_xpm = icon_tiff.replace(".tiff", ".xpm")
            if os.path.exists(icon_xpm):
                icon_path = icon_xpm
            else:
                print("No suitable icon found")
                sys.exit(1)
    else:
        print(f"Icon not found: {icon_tiff}")
        sys.exit(1)
    
    app = SystrayIcon(icon_path)
    app.run()

if __name__ == "__main__":
    main()