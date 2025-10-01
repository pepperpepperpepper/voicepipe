"""Optional systray integration for voicepipe."""

import sys
import os
import threading
import logging

logger = logging.getLogger(__name__)

class SystrayManager:
    """Manages systray icon if available, fails gracefully if not."""
    
    def __init__(self):
        self.icon = None
        self.available = False
        self._check_availability()
    
    def _check_availability(self):
        """Check if systray is available on this platform."""
        try:
            import pystray
            from PIL import Image
            self.available = True
            self._pystray = pystray
            self._Image = Image
        except ImportError:
            logger.debug("Systray not available (pystray/PIL not installed)")
            self.available = False
            return
        
        # The pystray library itself should handle whether a display is available.
        # Removing the explicit DISPLAY check for Linux as it might be too restrictive
        # (e.g., for Wayland or other setups) and pystray might have its own fallbacks
        # or detection mechanisms. If pystray fails to initialize, it will be caught
        # during the icon.run() attempt or earlier.
        # logger.debug(f"Platform: {sys.platform}, Display: {os.environ.get('DISPLAY')}")

    def show(self, daemon_instance=None, icon_path=None):
        """Show systray icon if available.

        Args:
            daemon_instance: An optional reference to the daemon, to allow UI actions.
            icon_path: Optional path to a custom icon image.
        """
        if not self.available:
            return False
        
        # Store daemon instance for callbacks like _on_cancel
        self.daemon_instance = daemon_instance

        try:
            # Create icon
            if icon_path and os.path.exists(icon_path):
                image = self._Image.open(icon_path)
            else:
                # Create default icon (red circle for recording)
                image = self._create_default_icon()
            
            # Create menu
            menu = self._pystray.Menu(
                self._pystray.MenuItem("Recording...", lambda: None, enabled=False),
                self._pystray.MenuItem("Cancel", self._on_cancel),
            )
            
            self.icon = self._pystray.Icon("voicepipe", image, "Voicepipe Recording", menu)
            
            # Run in thread to not block
            thread = threading.Thread(target=self.icon.run, daemon=True)
            thread.start()
            
            return True
            
        except Exception as e:
            logger.debug(f"Failed to show systray: {e}")
            self.available = False
            return False
    
    def hide(self):
        """Hide systray icon if shown."""
        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
            self.icon = None
    
    def _create_default_icon(self):
        """Create a default recording icon."""
        width = 48
        height = 48
        image = self._Image.new('RGBA', (width, height), (0, 0, 0, 0))
        
        try:
            from PIL import ImageDraw
            draw = ImageDraw.Draw(image)
            # Red circle
            draw.ellipse([4, 4, width-4, height-4], fill=(255, 0, 0, 255))
        except:
            pass
        
        return image
    
    def _on_cancel(self, icon, item):
        """Handle cancel from systray."""
        logger.info("Cancel requested from systray via menu.")
        if hasattr(self, 'daemon_instance') and self.daemon_instance:
            logger.info("Calling daemon's cancel_recording_via_ui method.")
            # Ensure this method exists on the daemon and is safe to call
            if hasattr(self.daemon_instance, 'cancel_recording_via_ui'):
                self.daemon_instance.cancel_recording_via_ui()
            else:
                logger.warning("Daemon instance does not have 'cancel_recording_via_ui' method.")
        else:
            logger.warning("No daemon_instance available to process cancel request from systray.")

# Global instance
_systray = None

def get_systray():
    """Get the global systray manager."""
    global _systray
    if _systray is None:
        _systray = SystrayManager()
    return _systray