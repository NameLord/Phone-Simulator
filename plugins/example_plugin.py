"""Example third-party app: a tiny coin flip.

Copy this file as a starting point. It declares a MANIFEST dict, exports an
App subclass and optionally a draw_icon(surface, rect, theme) function.
"""

from __future__ import annotations

import random

import pygame

from apps.base_app import App
from core.input import EventKind
from ui.paint import blit_text, draw_rounded

MANIFEST = {
    "app_id": "coin_flip",
    "name": "Coin Flip",
    "description": "A minimal plugin example that flips a virtual coin.",
    "category": "Plugins",
    "power_draw": 24.0,
}


class CoinFlipApp(App):
    app_id = "coin_flip"
    name = "Coin Flip"

    def __init__(self, context) -> None:
        super().__init__(context)
        self._result = "Tap to flip"
        self._tosses = 0

    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        area = self.content.inflate(-40, -40)
        draw_rounded(surface, area, fill=pal.surface, stroke=pal.stroke_soft, radius=28)
        blit_text(surface, self.font(24, True), "Coin Flip", pal.text, center=(area.centerx, area.y + 54))
        circle = pygame.Rect(0, 0, min(150, area.w - 60), min(150, area.h - 170))
        circle.center = area.center
        pygame.draw.circle(surface, pal.accent_soft, circle.center, circle.w // 2)
        pygame.draw.circle(surface, pal.accent, circle.center, circle.w // 2, 3)
        label = "HEADS" if self._result == "heads" else ("TAILS" if self._result == "tails" else "TAP")
        blit_text(surface, self.font(26, True), label, pal.accent_ink, center=circle.center)
        detail = self._result if self._result in ("heads", "tails") else "Tap the coin to flip"
        blit_text(surface, self.font(14), detail.title() if detail in ("heads", "tails") else detail, pal.text_secondary, center=(area.centerx, circle.bottom + 36))
        blit_text(surface, self.font(11), f"{self._tosses} tosses", pal.text_muted, center=(area.centerx, area.bottom - 28))

    def handle(self, event) -> bool:
        if event.kind is EventKind.TAP:
            self._result = random.choice(("heads", "tails"))
            self._tosses += 1
            return True
        return False

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.accent, radius=16)
    pygame.draw.circle(surface, pal.accent, rect.center, max(8, rect.w // 3), 2)
    blit_text(surface, theme.font(max(10, rect.h // 3), True), "C", pal.accent_ink, center=rect.center)
