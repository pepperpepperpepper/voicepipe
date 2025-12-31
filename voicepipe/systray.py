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
        # On Linux, a systray requires a graphical session. Importing pystray can
        # raise exceptions (e.g. DisplayNameError) when no display is available,
        # so check environment first.
        if sys.platform.startswith("linux"):
            display = os.environ.get("DISPLAY")
            wayland_display = os.environ.get("WAYLAND_DISPLAY")
            if not display and not wayland_display:
                logger.debug("Systray not available (no DISPLAY/WAYLAND_DISPLAY)")
                self.available = False
                return

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
        except Exception as e:
            logger.debug("Systray not available (pystray import failed): %s", e)
            self.available = False
            return
        
        # Double-check display on Linux (defensive; env can change in tests).
        if sys.platform.startswith("linux"):
            display = os.environ.get("DISPLAY")
            wayland_display = os.environ.get("WAYLAND_DISPLAY")
            if not display and not wayland_display:
                logger.debug("Systray not available (no DISPLAY/WAYLAND_DISPLAY)")
                self.available = False
                return
    
    def show(self, icon_path=None):
        """Show systray icon if available."""
        if not self.available:
            return False
        
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
        # Avoid blocking the UI thread.
        def _send_cancel():
            try:
                from .ipc import try_send_request

                resp = try_send_request("cancel")
                if not resp:
                    logger.warning("Systray cancel failed: daemon unavailable")
                    return
                if resp.get("error"):
                    logger.warning("Systray cancel failed: %s", resp.get("error"))
                    return
                logger.info("Systray cancel requested")
            except Exception as e:
                logger.exception("Systray cancel error: %s", e)

        threading.Thread(target=_send_cancel, daemon=True).start()

# Global instance
_systray = None

def get_systray():
    """Get the global systray manager."""
    global _systray
    if _systray is None:
        _systray = SystrayManager()
    return _systray
