"""Optional systray integration for voicepipe."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import os
import sys
import shutil
import subprocess
import threading
import logging
from pathlib import Path
from collections.abc import Callable

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


def _make_recording_pixmap(*, size: int = 32) -> list[object]:
    """Return a simple red-dot ARGB32 icon for StatusNotifierItem IconPixmap."""
    if size < 8:
        size = 8
    cx = (size - 1) / 2.0
    cy = (size - 1) / 2.0
    radius = max(1.0, (size / 2.0) - 2.0)
    r2 = radius * radius

    data = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            dx = x - cx
            dy = y - cy
            idx = (y * size + x) * 4
            if (dx * dx + dy * dy) <= r2:
                # ARGB
                data[idx : idx + 4] = bytes((255, 255, 0, 0))
            else:
                data[idx : idx + 4] = bytes((0, 0, 0, 0))
    # dbus-next expects structs to be expressed as Python lists (not tuples).
    return [size, size, bytes(data)]


def _can_use_sni() -> bool:
    """True if DBus StatusNotifierItem deps are present (Waybar-friendly)."""
    if not sys.platform.startswith("linux"):
        return False
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    return importlib.util.find_spec("dbus_next") is not None


class _SniTrayBackend:
    """StatusNotifierItem backend (integrates with Waybar's tray)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._error: Exception | None = None
        self._bus = None

    def show(self, *, on_activate: Callable[[], None] | None = None) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._ready.clear()
            self._stop_requested.clear()
            self._error = None
            self._bus = None
            self._thread = threading.Thread(
                target=self._run, args=(on_activate,), daemon=True
            )
            self._thread.start()
        # Keep this short; the systray must not slow down recording startup.
        self._ready.wait(timeout=0.35)
        # If we're still initializing, consider this a best-effort success and
        # avoid falling back to X11-only backends on Wayland.
        if not self._ready.is_set():
            return True
        return self._error is None and self._bus is not None

    def hide(self) -> None:
        self._stop_requested.set()
        with self._lock:
            loop = self._loop
        if not loop:
            return
        try:
            loop.call_soon_threadsafe(self._stop_in_loop)
        except Exception:
            return

    def _stop_in_loop(self) -> None:
        try:
            if self._bus is not None:
                self._bus.disconnect()
        except Exception:
            pass
        try:
            if self._loop is not None:
                self._loop.stop()
        except Exception:
            pass

    def _run(self, on_activate) -> None:
        try:
            from dbus_next.aio import MessageBus
            from dbus_next.constants import PropertyAccess
            from dbus_next.message import Message
            from dbus_next.signature import Variant
            from dbus_next.service import ServiceInterface, dbus_property, method, signal
        except Exception as e:
            self._error = e
            self._ready.set()
            return

        class VoicepipeDbusMenu(ServiceInterface):
            def __init__(self):
                super().__init__("com.canonical.dbusmenu")
                self._revision = 1

            @dbus_property(access=PropertyAccess.READ)
            def Version(self) -> "u":
                return 3

            @dbus_property(access=PropertyAccess.READ)
            def TextDirection(self) -> "s":
                return "ltr"

            @dbus_property(access=PropertyAccess.READ)
            def Status(self) -> "s":
                return "normal"

            def _layout_item(self, item_id: int, *, label: str, enabled: bool) -> list[object]:
                props = {
                    "label": Variant("s", label),
                    "enabled": Variant("b", bool(enabled)),
                    "visible": Variant("b", True),
                }
                # Layout struct: (id, properties, children)
                return [int(item_id), props, []]

            def _root_layout(self) -> list[object]:
                recording = self._layout_item(1, label="Recording…", enabled=False)
                cancel = self._layout_item(2, label="Cancel", enabled=True)
                children = [
                    Variant("(ia{sv}av)", recording),
                    Variant("(ia{sv}av)", cancel),
                ]
                return [0, {}, children]

            @method()
            def GetLayout(self, parent_id: "i", recursion_depth: "i", property_names: "as") -> "u(ia{sv}av)":  # noqa: N802
                layout = self._root_layout()
                return [self._revision, layout]

            @method()
            def AboutToShow(self, item_id: "i") -> "b":  # noqa: N802
                return False

            @method()
            def Event(self, item_id: "i", event_id: "s", data: "v", timestamp: "u"):  # noqa: N802
                if int(item_id) == 2 and str(event_id) in {"clicked", "activate"}:
                    if on_activate is None:
                        return
                    try:
                        threading.Thread(target=on_activate, daemon=True).start()
                    except Exception:
                        return

        class VoicepipeStatusNotifierItem(ServiceInterface):
            def __init__(self):
                super().__init__("org.kde.StatusNotifierItem")
                self._icon_pixmap = [_make_recording_pixmap(size=32)]

            @dbus_property(access=PropertyAccess.READ)
            def Category(self) -> "s":
                return "ApplicationStatus"

            @dbus_property(access=PropertyAccess.READ)
            def Id(self) -> "s":
                return "voicepipe"

            @dbus_property(access=PropertyAccess.READ)
            def Title(self) -> "s":
                return "Voicepipe"

            @dbus_property(access=PropertyAccess.READ)
            def Status(self) -> "s":
                return "Active"

            @dbus_property(access=PropertyAccess.READ)
            def WindowId(self) -> "i":
                return 0

            @dbus_property(access=PropertyAccess.READ)
            def IconName(self) -> "s":
                # Some tray hosts prefer themed icons when available.
                return "media-record"

            @dbus_property(access=PropertyAccess.READ)
            def IconThemePath(self) -> "s":
                return ""

            @dbus_property(access=PropertyAccess.READ)
            def IconPixmap(self) -> "a(iiay)":
                return self._icon_pixmap

            @dbus_property(access=PropertyAccess.READ)
            def OverlayIconName(self) -> "s":
                return ""

            @dbus_property(access=PropertyAccess.READ)
            def OverlayIconPixmap(self) -> "a(iiay)":
                return []

            @dbus_property(access=PropertyAccess.READ)
            def AttentionIconName(self) -> "s":
                return ""

            @dbus_property(access=PropertyAccess.READ)
            def AttentionIconPixmap(self) -> "a(iiay)":
                return []

            @dbus_property(access=PropertyAccess.READ)
            def AttentionMovieName(self) -> "s":
                return ""

            @dbus_property(access=PropertyAccess.READ)
            def ToolTip(self) -> "(sa(iiay)ss)":
                # dbus-next expects structs to be expressed as Python lists.
                return ["", self._icon_pixmap, "Voicepipe", "Recording…"]

            @dbus_property(access=PropertyAccess.READ)
            def ItemIsMenu(self) -> "b":
                return False

            @dbus_property(access=PropertyAccess.READ)
            def Menu(self) -> "o":
                return "/StatusNotifierItem/Menu"

            @signal()
            def NewIcon(self):  # noqa: N802
                return None

            @signal()
            def NewStatus(self) -> "s":  # noqa: N802
                return "Active"

            @signal()
            def NewToolTip(self):  # noqa: N802
                return None

            @method()
            def Activate(self, x: "i", y: "i"):  # noqa: N802
                if on_activate is None:
                    return
                try:
                    threading.Thread(target=on_activate, daemon=True).start()
                except Exception:
                    return

            @method()
            def ContextMenu(self, x: "i", y: "i"):  # noqa: N802
                return

            @method()
            def SecondaryActivate(self, x: "i", y: "i"):  # noqa: N802
                return

            @method()
            def Scroll(self, delta: "i", orientation: "s"):  # noqa: N802
                return

        async def _main() -> None:
            bus = await MessageBus().connect()
            self._bus = bus
            menu = VoicepipeDbusMenu()
            bus.export("/StatusNotifierItem/Menu", menu)
            item = VoicepipeStatusNotifierItem()
            bus.export("/StatusNotifierItem", item)

            # Register using our unique sender name by passing an object path.
            msg = Message(
                destination="org.kde.StatusNotifierWatcher",
                path="/StatusNotifierWatcher",
                interface="org.kde.StatusNotifierWatcher",
                member="RegisterStatusNotifierItem",
                signature="s",
                body=["/StatusNotifierItem"],
            )
            await bus.call(msg)

        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_main())
            self._ready.set()
            if self._stop_requested.is_set():
                return
            loop.run_forever()
        except Exception as e:
            self._error = e
            self._ready.set()
        finally:
            with contextlib.suppress(Exception):
                if self._bus is not None:
                    self._bus.disconnect()
            with contextlib.suppress(Exception):
                loop.stop()
            with contextlib.suppress(Exception):
                loop.close()
            with self._lock:
                self._loop = None
                self._thread = None
                self._bus = None


class SystrayManager:
    """Manages systray icon if available, fails gracefully if not."""
    
    def __init__(self):
        self.icon = None
        self._sni = _SniTrayBackend() if _can_use_sni() else None
        self._active_backend: str | None = None
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

        # Prefer a Wayland-friendly StatusNotifierItem backend when possible
        # (e.g. Sway+Waybar). This avoids XEmbed-only systrays.
        if self._sni is not None:
            self.available = True
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

        # On Linux, prefer SNI if available; it integrates with Waybar's tray.
        if sys.platform.startswith("linux") and self._sni is not None:
            try:
                if self._sni.show(on_activate=self._request_cancel):
                    self._active_backend = "sni"
                    return True
            except Exception as e:
                logger.debug("Failed to show SNI systray: %s", e)
        
        try:
            if not hasattr(self, "_pystray") or not hasattr(self, "_Image"):
                # Lazily import so SNI can work without pystray/Pillow installed.
                import pystray
                from PIL import Image
                self._pystray = pystray
                self._Image = Image

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
            
            self._active_backend = "pystray"
            return True
            
        except Exception as e:
            logger.debug(f"Failed to show systray: {e}")
            self.available = False
            return False
    
    def hide(self):
        """Hide systray icon if shown."""
        if self._active_backend == "sni" and self._sni is not None:
            try:
                self._sni.hide()
            except Exception:
                pass
            self._active_backend = None
            return

        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
            self.icon = None
        self._active_backend = None
    
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
    
    def _request_cancel(self) -> None:
        """Request cancel from systray (thread-safe)."""
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

    def _on_cancel(self, icon, item):
        """Handle cancel from pystray menu."""
        self._request_cancel()

# Global instance
_systray = None

def get_systray():
    """Get the global systray manager."""
    global _systray
    if _systray is None:
        _systray = SystrayManager()
    return _systray
