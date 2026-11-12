"""App platform base types.

An app receives an :class:`AppContext` (never the kernel), draws into its own
``Surface`` and is clipped to the content area by the host scene. Lifecycle:

    on_launch()        first time the instance is created
    on_resume()        brought back from Recents
    on_pause()         sent to the background
    on_close()         removed from Recents / uninstalled / app quit
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, ClassVar

import pygame

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from core.input import PointerEvent
    from services.app_manager import AppManager
    from services.audio import AudioService
    from services.clipboard import ClipboardService
    from services.network import NetworkService
    from services.notifications import NotificationCenter
    from services.photo_library import PhotoLibrary
    from services.profiler import Profiler
    from services.schedules import ScheduleService
    from services.settings import SettingsService
    from services.storage import Storage
    from services.telephony import TelephonyService
    from ui.theme import Palette, Theme

RGB = tuple[int, int, int]

#: Height of the status bar plus the per-app toolbar. The host uses it and the
#: app manager passes it to every AppContext so apps can lay out below chrome.
APP_CHROME_HEIGHT = 118


@dataclass
class AppContext:
    """Everything an app is allowed to touch."""

    settings: "SettingsService"
    storage: "Storage"
    theme: "Theme"
    clock: "DeviceClock"
    notifications: "NotificationCenter"
    apps: "AppManager"
    size: tuple[int, int]
    chrome_height: int = APP_CHROME_HEIGHT
    telephony: "TelephonyService | None" = None
    schedules: "ScheduleService | None" = None
    audio: "AudioService | None" = None
    photos: "PhotoLibrary | None" = None
    profiler: "Profiler | None" = None
    clipboard: "ClipboardService | None" = None
    network: "NetworkService | None" = None
    _notify: Callable[[str, str, str], None] | None = field(default=None, repr=False)
    _share: Callable[[dict], bool] | None = field(default=None, repr=False)
    _toast: Callable[[str, str, Callable[[], None]], object] | None = field(default=None, repr=False)
    close_requested: bool = False
    viewport: pygame.Rect | None = None

    @property
    def palette(self) -> "Palette":
        return self.theme.palette

    def notify(self, app_id: str, title: str, body: str) -> None:
        if self._notify is not None:
            self._notify(app_id, title, body)

    def request_close(self) -> None:
        self.close_requested = True

    def share(self, payload: dict) -> bool:
        """Ask the host to open the system share sheet."""
        if self._share is not None:
            return bool(self._share(payload))
        return False

    def undo_toast(self, message: str, callback: Callable[[], None]) -> None:
        if self._toast is not None:
            self._toast(message, "Undo", callback)


class App(ABC):
    """Base class for every installed application."""

    app_id: ClassVar[str] = ""
    name: ClassVar[str] = ""

    def __init__(self, context: AppContext) -> None:
        self.ctx = context
        self.size = context.size
        self.content = pygame.Rect(
            0,
            context.chrome_height,
            context.size[0],
            max(1, context.size[1] - context.chrome_height),
        )
        self.surface = pygame.Surface(context.size)
        self.surface.fill(context.palette.bg_bottom)
        self._app_elapsed = 0.0
        self._full_size = (int(context.size[0]), int(context.size[1]))
        self._view_viewport: pygame.Rect | None = None

    # -- resize --------------------------------------------------------------
    def resize(self, size: tuple[int, int]) -> None:
        """Keep the instance alive while the logical canvas rotates."""
        new_size = (int(size[0]), int(size[1]))
        self._full_size = new_size
        if self._view_viewport is not None:
            self.set_viewport(self._view_viewport)
            return
        if new_size == self.size:
            return
        self.size = new_size
        self.content = pygame.Rect(
            0,
            self.ctx.chrome_height,
            new_size[0],
            max(1, new_size[1] - self.ctx.chrome_height),
        )
        self.surface = pygame.Surface(new_size)
        self.surface.fill(self.palette.bg_bottom)
        self.on_resize()

    def set_viewport(self, viewport: pygame.Rect | None) -> None:
        """Render into a sub-rectangle for split view.

        ``None`` restores the full logical canvas. Apps receive a smaller
        surface and a content rect below the host's per-pane chrome.
        """
        if viewport is not None:
            rect = pygame.Rect(viewport)
            rect.w = max(120, rect.w)
            rect.h = max(120, rect.h)
            self._view_viewport = rect
            self.ctx.viewport = rect
            self.size = (rect.w, rect.h)
            pane_chrome = 58
            self.content = pygame.Rect(0, pane_chrome, rect.w, max(1, rect.h - pane_chrome))
        else:
            self._view_viewport = None
            self.ctx.viewport = None
            self.size = self._full_size
            self.content = pygame.Rect(
                0,
                self.ctx.chrome_height,
                self.size[0],
                max(1, self.size[1] - self.ctx.chrome_height),
            )
        self.surface = pygame.Surface(self.size)
        self.surface.fill(self.palette.bg_bottom)
        self.on_resize()

    def on_resize(self) -> None:
        """Rebuild layout-sensitive caches after :meth:`resize`."""

    # -- convenience ---------------------------------------------------------
    @property
    def theme(self) -> "Theme":
        return self.ctx.theme

    @property
    def palette(self) -> "Palette":
        return self.ctx.theme.palette

    def font(self, size: int, bold: bool = False) -> pygame.font.Font:
        return self.ctx.theme.font(size, bold)

    def mono(self, size: int, bold: bool = False) -> pygame.font.Font:
        return self.ctx.theme.mono(size, bold)

    def notify(self, title: str, body: str) -> None:
        self.ctx.notify(self.app_id, title, body)

    @property
    def clipboard(self):
        return self.ctx.clipboard

    @property
    def network(self):
        return self.ctx.network

    @property
    def log(self):
        from services.logger import get_logger

        return get_logger(f"app.{self.app_id}")

    def copy_text(self, text: str) -> bool:
        return bool(self.ctx.clipboard.copy(text)) if self.ctx.clipboard is not None else False

    def paste_text(self) -> str:
        return self.ctx.clipboard.paste() if self.ctx.clipboard is not None else ""

    # -- deep links / sharing -------------------------------------------------
    def open_payload(self, payload: dict) -> None:
        """Handle a shared item or search deep link."""

    def share(self, payload: dict) -> bool:
        return self.ctx.share(payload)

    def clipboard_copy(self) -> bool:
        """Override in apps that support Ctrl+C."""
        return False

    def clipboard_paste(self) -> bool:
        """Override in apps that support Ctrl+V."""
        return False

    # -- lifecycle -----------------------------------------------------------
    def on_launch(self) -> None:
        """Called once when the instance is first created."""

    def on_pause(self) -> None:
        """Called when the app is backgrounded; keep all state."""

    def on_resume(self) -> None:
        """Called when the app returns to the foreground."""

    def on_close(self) -> None:
        """Called when the app is closed or uninstalled; flush state here."""

    # -- frame ---------------------------------------------------------------
    def update(self, dt: float) -> None:
        self._app_elapsed += max(0.0, float(dt))

    @abstractmethod
    def draw(self, surface: pygame.Surface) -> None:
        """Draw into the app's own full-size surface."""

    def handle(self, event: "PointerEvent") -> bool:
        return False

    @abstractmethod
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        """Draw the home-screen icon into ``rect``."""

    @property
    def elapsed(self) -> float:
        return self._app_elapsed

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.app_id!r})"
