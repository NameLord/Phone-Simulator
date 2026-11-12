"""System status bar drawn by the kernel on top of every scene.

It owns the live clock, the procedural signal / wifi glyphs, the battery icon
driven by :class:`core.clock.DeviceClock`, and the unread notification badge.
Scenes must not draw it themselves, which keeps spacing identical everywhere.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pygame

from ui.layout import layout
from ui.paint import blit_text, draw_bell, with_alpha

if TYPE_CHECKING:
    from core.clock import DeviceClock
    from ui.theme import Palette, Theme

RGB = tuple[int, int, int]


class StatusBar:
    """54 virtual pixels of system chrome."""

    HEIGHT = 54

    def __init__(self) -> None:
        self._bell_phase = 0.0
        self._time_key: tuple[str, tuple[object, ...]] | None = None
        self._time_image: pygame.Surface | None = None

    @property
    def height(self) -> int:
        return self.HEIGHT

    def update(self, dt: float) -> None:
        self._bell_phase = (self._bell_phase + max(0.0, dt)) % 4.0

    # -- system buttons -------------------------------------------------------
    @staticmethod
    def _power_rect(width: int) -> pygame.Rect:
        return pygame.Rect(width - 30, 10, 24, 30)

    @staticmethod
    def _volume_up_rect(width: int) -> pygame.Rect:
        return pygame.Rect(width - 56, 14, 18, 22)

    @staticmethod
    def _volume_down_rect(width: int) -> pygame.Rect:
        return pygame.Rect(width - 76, 14, 18, 22)

    @staticmethod
    def _battery_rect(width: int) -> pygame.Rect:
        return pygame.Rect(width - 124, 14, 40, 26)

    def hit_test(self, pos: tuple[float, float], width: int) -> str | None:
        """Return the system action under a virtual point, if any."""
        if self._power_rect(width).collidepoint(pos):
            return "power"
        if self._volume_up_rect(width).collidepoint(pos):
            return "vol_up"
        if self._volume_down_rect(width).collidepoint(pos):
            return "vol_down"
        if self._battery_rect(width).collidepoint(pos):
            return "battery"
        return None

    def draw(
        self,
        surface: pygame.Surface,
        theme: "Theme",
        clock: "DeviceClock",
        unread: int = 0,
        network=None,
    ) -> None:
        pal: "Palette" = theme.palette

        # The clock changes once a minute; render it only when it (or the
        # theme) changes instead of allocating a text surface every frame.
        label = clock.time_string()
        key = (label, theme.signature)
        if key != self._time_key or self._time_image is None:
            self._time_key = key
            self._time_image = theme.font(20, True).render(label, True, pal.text)
        rect = self._time_image.get_rect(midleft=(layout.margin + 4, 28))
        surface.blit(self._time_image, rect)

        # Camera island.
        island = pygame.Rect(0, 0, min(96, max(56, surface.get_width() // 6)), 24)
        island.center = (surface.get_width() // 2, 22)
        pygame.draw.rect(surface, with_alpha(pal.text, 28), island, border_radius=12)
        pygame.draw.circle(surface, with_alpha(pal.text, 70), (island.centerx - 12, island.centery), 4)
        pygame.draw.circle(surface, with_alpha(pal.text, 40), (island.centerx + 12, island.centery), 4)

        width = surface.get_width()
        right = width - 90

        # Power and volume buttons are live controls, drawn as flat glyphs.
        self._draw_power(surface, pal, self._power_rect(width))
        self._draw_volume(surface, pal, self._volume_up_rect(width), up=True)
        self._draw_volume(surface, pal, self._volume_down_rect(width), up=False)

        # Unread badge.
        if unread > 0:
            badge_center = (right - 118, 27)
            pygame.draw.circle(surface, pal.accent, badge_center, 10)
            label = "9+" if unread > 9 else str(unread)
            blit_text(
                surface,
                theme.font(11, True),
                label,
                pal.on_accent,
                center=badge_center,
            )

        wifi_on = True if network is None else bool(getattr(network, "wifi_enabled", True))
        airplane = bool(getattr(network, "airplane", False)) if network is not None else False
        self._draw_wifi(surface, pal, (right - 86, 27), pal.text if wifi_on else pal.text_muted)
        if not wifi_on:
            pygame.draw.line(surface, pal.danger, (right - 95, 18), (right - 77, 36), 2)
        self._draw_signal(surface, pal, right - 60, 32, pal.text_muted if airplane else pal.text)
        if airplane:
            blit_text(surface, theme.font(8, True), "AIR", pal.warning, topleft=(right - 74, 16), tracking=0.6)
        self._draw_battery(surface, pal, clock, right)

    def _draw_power(self, surface, pal: "Palette", rect: pygame.Rect) -> None:
        center = rect.center
        pygame.draw.circle(surface, pal.text, center, 7, 2)
        pygame.draw.rect(
            surface,
            pal.text,
            pygame.Rect(center[0] - 1, center[1] - 9, 2, 8),
        )

    def _draw_volume(self, surface, pal: "Palette", rect: pygame.Rect, *, up: bool) -> None:
        cx, cy = rect.center
        pygame.draw.rect(surface, pal.text, pygame.Rect(cx - 7, cy - 3, 5, 7), border_radius=1)
        pygame.draw.polygon(
            surface,
            pal.text,
            [(cx - 3, cy - 3), (cx + 2, cy - 7), (cx + 2, cy + 7), (cx - 3, cy + 3)],
        )
        if up:
            pygame.draw.arc(surface, pal.text, pygame.Rect(cx + 2, cy - 8, 10, 16), -1.0, 1.0, 2)
        else:
            pygame.draw.arc(surface, pal.text, pygame.Rect(cx + 2, cy - 8, 10, 16), 2.14, 4.14, 2)

    # -- glyphs ---------------------------------------------------------------
    def _draw_wifi(
        self,
        surface: pygame.Surface,
        pal: "Palette",
        center: tuple[int, int],
        color: RGB,
    ) -> None:
        cx, cy = center
        for radius in (5, 10, 15):
            rect = pygame.Rect(cx - radius, cy - radius, radius * 2, radius * 2)
            pygame.draw.arc(surface, color, rect, 0.85, 2.30, 2)
        pygame.draw.circle(surface, color, (cx, cy + 1), 2)

    def _draw_signal(
        self,
        surface: pygame.Surface,
        pal: "Palette",
        x: int,
        baseline: int,
        color: RGB,
    ) -> None:
        for index in range(4):
            bar_height = 4 + index * 4
            pygame.draw.rect(
                surface,
                color,
                pygame.Rect(x + index * 6, baseline - bar_height, 4, bar_height),
                border_radius=1,
            )

    def _draw_battery(
        self,
        surface: pygame.Surface,
        pal: "Palette",
        clock: "DeviceClock",
        right: int,
    ) -> None:
        body = pygame.Rect(right - 34, 18, 34, 18)
        accent = pal.text
        percent = clock.battery_percent
        if percent <= 15.0:
            accent = pal.danger
        elif percent <= 30.0:
            accent = pal.warning

        pygame.draw.rect(surface, accent, body, width=2, border_radius=5)
        pygame.draw.rect(
            surface,
            accent,
            pygame.Rect(body.right + 2, body.centery - 3, 3, 6),
            border_radius=1,
        )
        inner = body.inflate(-6, -6)
        filled = int(inner.w * max(0.0, min(1.0, percent / 100.0)))
        if filled > 0:
            pygame.draw.rect(
                surface,
                accent,
                pygame.Rect(inner.x, inner.y, filled, inner.h),
                border_radius=2,
            )
        if clock.charging:
            pulse = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(self._bell_phase * 3.6))
            pygame.draw.arc(
                surface,
                with_alpha(pal.success, int(110 + 100 * pulse)),
                body.inflate(8, 8),
                -0.4,
                3.6,
                1,
            )
            bolt = [
                (body.centerx + 2, body.y + 3),
                (body.centerx - 4, body.centery + 1),
                (body.centerx, body.centery + 1),
                (body.centerx - 2, body.bottom - 3),
                (body.centerx + 5, body.centery - 1),
                (body.centerx + 1, body.centery - 1),
            ]
            pygame.draw.polygon(surface, pal.surface, bolt)

    def draw_badge_bell(self, surface: pygame.Surface, theme: "Theme", origin: tuple[int, int]) -> None:
        """Optional bell glyph reused by settings' notification row."""
        draw_bell(surface, origin, theme.palette.text_secondary, scale=0.7)

    def __repr__(self) -> str:
        return f"StatusBar(height={self.HEIGHT})"
