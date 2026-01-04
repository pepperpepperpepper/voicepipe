"""Optional systray integration for voicepipe."""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import threading
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _set_env_if_missing(name: str, value: str | None) -> None:
    if not value:
        return
    if os.environ.get(name):
        return
    os.environ[name] = value


def _load_gui_env_from_systemd(*, timeout_s: float = 0.25) -> None:
    """Best-effort import DISPLAY/XAUTHORITY/WAYLAND_DISPLAY from systemd --user.

    This is important for user services started before the graphical session
    fully propagates environment variables into systemd.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    try:
        proc = subprocess.run(
            [systemctl, "--user", "show-environment"],
            capture_output=True,
            text=True,
            check=False,
            timeout=float(timeout_s),
        )
    except Exception:
        return
    if proc.returncode != 0:
        return
    for line in (proc.stdout or "").splitlines():
        if "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        if key in {"DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"}:
            _set_env_if_missing(key, value.strip())


def _infer_display_from_x11_socket() -> str | None:
    """Infer an X11 DISPLAY from /tmp/.X11-unix when possible (e.g. :0)."""
    x11_dir = Path("/tmp/.X11-unix")
    if not x11_dir.is_dir():
        return None
    candidates: list[int] = []
    try:
        for entry in x11_dir.iterdir():
            name = entry.name
            if not name.startswith("X"):
                continue
            suffix = name[1:]
            if not suffix.isdigit():
                continue
            candidates.append(int(suffix))
    except Exception:
        return None
    if not candidates:
        return None
    return f":{min(candidates)}"


def _infer_wayland_display() -> str | None:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return None
    base = Path(runtime_dir)
    for name in ("wayland-0", "wayland-1"):
        try:
            if (base / name).exists():
                return name
        except Exception:
            continue
    return None


def _ensure_gui_environment() -> None:
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return

    _load_gui_env_from_systemd()

    if not os.environ.get("DISPLAY"):
        _set_env_if_missing("DISPLAY", _infer_display_from_x11_socket())

    if not os.environ.get("XAUTHORITY"):
        try:
            candidate = Path.home() / ".Xauthority"
            if candidate.exists():
                _set_env_if_missing("XAUTHORITY", str(candidate))
        except Exception:
            pass

    if not os.environ.get("WAYLAND_DISPLAY"):
        _set_env_if_missing("WAYLAND_DISPLAY", _infer_wayland_display())


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
            _ensure_gui_environment()
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
            _ensure_gui_environment()
            display = os.environ.get("DISPLAY")
            wayland_display = os.environ.get("WAYLAND_DISPLAY")
            if not display and not wayland_display:
                logger.debug("Systray not available (no DISPLAY/WAYLAND_DISPLAY)")
                self.available = False
                return
    
    def show(self, icon_path=None):
        """Show systray icon if available."""
        if not self.available:
            # The daemon process may have started before the graphical session
            # environment was available. Re-check on demand.
            self._check_availability()
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
