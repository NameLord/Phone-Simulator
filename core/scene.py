"""Scene base class and shared animation math.

A scene is a self-contained screen. The kernel owns the lifecycle:

    factory(kernel) -> scene
    scene.on_enter() -> update(dt)* -> scene.draw(canvas)
    scene.on_exit() -> discarded

Scenes request navigation through :meth:`Scene.go`, which delegates to the
kernel's transition manager, so no scene ever imports another scene.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

import pygame

if TYPE_CHECKING:
    from core.input import PointerEvent
    from core.kernel import Kernel
    from services.notifications import NotificationCenter
    from services.settings import SettingsService
    from ui.theme import Theme


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def smoothstep(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def ease_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3


def ease_in_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    return t ** 3


def ease_in_out_cubic(t: float) -> float:
    t = clamp(t, 0.0, 1.0)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def pulse(t: float, period: float = 1.0) -> float:
    """0..1 sine pulse, useful for breathing accents."""
    if period <= 0.0:
        return 0.0
    return 0.5 + 0.5 * math.sin(t / period * math.tau)


class Scene(ABC):
    """Base class for every screen in the OS."""

    name: ClassVar[str] = "scene"
    # Scenes may hide system chrome (the boot splash does).
    shows_status_bar: ClassVar[bool] = True

    def __init__(self, kernel: "Kernel") -> None:
        self.kernel = kernel
        self.registry = kernel.registry
        self.display = kernel.display
        self.device_clock = kernel.device_clock
        self.input = kernel.input
        self._elapsed = 0.0

    # -- lifecycle ----------------------------------------------------------
    def on_enter(self) -> None:
        """Called once, right before the first update."""
        self._elapsed = 0.0

    def on_exit(self) -> None:
        """Called once when the scene is replaced. Release caches here."""

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        """Called after an orientation change; rebuild cached surfaces."""

    def update(self, dt: float) -> None:
        """Advance scene state. Always called with a fixed dt."""
        self._elapsed += dt

    @abstractmethod
    def draw(self, surface: pygame.Surface) -> None:
        """Render into the virtual canvas."""

    def handle(self, event: "PointerEvent") -> bool:
        """Handle one decoded input event; return True when consumed."""
        return False

    # -- helpers ------------------------------------------------------------
    @property
    def elapsed(self) -> float:
        return self._elapsed

    @property
    def size(self) -> tuple[int, int]:
        return self.display.virtual_size

    @property
    def center(self) -> tuple[int, int]:
        width, height = self.display.virtual_size
        return (width // 2, height // 2)

    # -- live services -------------------------------------------------------
    @property
    def theme(self) -> "Theme":
        return self.registry.require("theme")

    @property
    def settings(self) -> "SettingsService":
        return self.registry.require("settings")

    @property
    def notifications(self) -> "NotificationCenter":
        return self.registry.require("notifications")

    def font(self, size: int, bold: bool = False) -> pygame.font.Font:
        """Theme-scaled font; falls back to the display before services wire up."""
        theme = self.registry.get("theme")
        if theme is not None:
            return theme.font(size, bold)
        return self.display.font(size, bold)

    def mono(self, size: int, bold: bool = False) -> pygame.font.Font:
        theme = self.registry.get("theme")
        if theme is not None:
            return theme.mono(size, bold)
        return self.display.mono_font(size, bold)

    def go(self, name: str, fade: float = 0.25) -> None:
        """Ask the kernel to transition to another registered scene."""
        self.kernel.switch_scene(name, fade=fade)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(elapsed={self._elapsed:.2f}s)"
