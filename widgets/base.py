"""Widget primitives and shared rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygame

from ui.paint import blit_text, draw_rounded

if TYPE_CHECKING:
    from services.audio import AudioService
    from services.clock import DeviceClock
    from services.power import PowerService
    from services.settings import SettingsService
    from services.storage import Storage
    from ui.theme import Theme


@dataclass
class WidgetContext:
    theme: "Theme"
    clock: "DeviceClock"
    settings: "SettingsService"
    power: "PowerService | None" = None
    audio: "AudioService | None" = None
    storage: "Storage | None" = None

    @property
    def palette(self):
        return self.theme.palette

    def font(self, size: int, bold: bool = False):
        return self.theme.font(size, bold)

    def mono(self, size: int, bold: bool = False):
        return self.theme.mono(size, bold)


class BaseWidget:
    """One live widget instance renderer."""

    kind = "base"
    label = "Widget"

    def __init__(self, options: dict | None = None) -> None:
        self.options = options or {}

    def update(self, dt: float, ctx: WidgetContext) -> None:
        return None

    def draw(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext) -> None:
        self.panel(surface, rect, ctx)

    def panel(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext, *, alpha: int = 176) -> None:
        draw_rounded(
            surface,
            rect,
            fill=ctx.palette.surface,
            stroke=ctx.palette.stroke_soft,
            radius=22,
            alpha=alpha,
        )

    def title(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext, text: str, *, icon_color=None) -> None:
        font = ctx.font(10, bold=True)
        blit_text(surface, font, text.upper(), icon_color or ctx.palette.text_muted, topleft=(rect.x + 16, rect.y + 12), tracking=1)

    def settings_rows(self) -> list[tuple[str, str, object, tuple[object, ...]]]:
        """Optional ``(key, label, value, choices)`` rows for Settings."""
        return []
