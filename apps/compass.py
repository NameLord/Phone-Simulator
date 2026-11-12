"""Compass: a drifting heading, level bubble and fake calibration spin."""

from __future__ import annotations

import math

import numpy as np
import pygame

from apps._kit import draw_button
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, with_alpha

APP_ID = "compass"
APP_NAME = "Compass"
CALIBRATION_SECONDS = 2.0


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    center = rect.center
    radius = max(8, int(rect.w * 0.31))
    pygame.draw.circle(surface, pal.text_muted, center, radius, max(1, rect.w // 30))
    pygame.draw.polygon(
        surface,
        pal.danger,
        [
            (center[0], center[1] - radius + 2),
            (center[0] - max(3, rect.w // 12), center[1] + max(2, rect.h // 12)),
            (center[0] + max(3, rect.w // 12), center[1] + max(2, rect.h // 12)),
        ],
    )
    pygame.draw.polygon(
        surface,
        pal.text_secondary,
        [
            (center[0], center[1] + radius - 2),
            (center[0] - max(3, rect.w // 12), center[1] - max(2, rect.h // 12)),
            (center[0] + max(3, rect.w // 12), center[1] - max(2, rect.h // 12)),
        ],
    )


class CompassApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._rng = np.random.default_rng(0xC0FFEE)
        self._heading = 32.0
        self._velocity = 0.0
        self._bubble = [0.0, 0.0]
        self._bubble_velocity = [0.0, 0.0]
        self._calibrating = 0.0
        self._phase = 0.0
        self._last_cardinal = ""

    def update(self, dt: float) -> None:
        super().update(dt)
        step = max(0.0, min(0.1, dt))
        self._phase += step
        if self._calibrating > 0.0:
            self._calibrating = max(0.0, self._calibrating - step)
            self._heading = (self._heading + step * 360.0) % 360.0
            self._velocity = 0.0
            if self._calibrating == 0.0:
                self._heading = float(self._rng.uniform(0.0, 360.0))
        else:
            self._velocity += float(self._rng.normal(0.0, 4.0)) * step
            self._velocity *= 0.965
            self._heading = (self._heading + self._velocity * step + math.sin(self._phase * 0.9) * 0.12) % 360.0
        # The level bubble has no hardware either, so it drifts on a slow walk.
        for axis in range(2):
            self._bubble_velocity[axis] += float(self._rng.normal(0.0, 0.55)) * step
            self._bubble_velocity[axis] *= 0.94
            self._bubble[axis] += self._bubble_velocity[axis] * step
            self._bubble[axis] = max(-1.0, min(1.0, self._bubble[axis]))
        cardinal = self._cardinal()
        if cardinal != self._last_cardinal:
            self._last_cardinal = cardinal

    def _cardinal(self) -> str:
        names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
        return names[int(((self._heading + 22.5) % 360.0) // 45.0)]

    # -- geometry -------------------------------------------------------------
    def _dial_center_radius(self) -> tuple[tuple[int, int], int]:
        if current_layout.is_landscape:
            center = (self.content.x + min(self.content.h - 80, int(self.content.w * 0.38)) // 2 + 12, self.content.centery - 8)
            radius = min(int(self.content.h * 0.32), int(self.content.w * 0.24))
        else:
            center = (self.content.centerx, self.content.y + int(self.content.h * 0.32))
            radius = min(int(self.content.w * 0.34), int(self.content.h * 0.24))
        return center, max(50, radius)

    def _calibrate_rect(self) -> pygame.Rect:
        width = min(220, self.content.w - 60)
        return pygame.Rect(self.content.centerx - width // 2, self.content.bottom - 64, width, 42)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self._start_calibration()
                return True
            return False
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if self._calibrate_rect().collidepoint(event.pos):
            self._start_calibration()
            return True
        return False

    def _start_calibration(self) -> None:
        if self._calibrating > 0.0:
            return
        self._calibrating = CALIBRATION_SECONDS

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        center, radius = self._dial_center_radius()
        self._draw_dial(surface, pal, center, radius)
        if current_layout.is_landscape:
            right_x = center[0] + radius + 30
            self._draw_readout(surface, pal, right_x, self.content.centery - 30)
        else:
            self._draw_readout(surface, pal, self.content.centerx, center[1] + radius + 40)
        button = self._calibrate_rect()
        if self._calibrating > 0.0:
            label = f"CALIBRATING {self._calibrating:.1f}s"
            color = pal.warning
        else:
            label = "CALIBRATE"
            color = pal.accent
        draw_button(surface, button, label, self.font(11, True), pal, fill=color, color=pal.on_accent, stroke=None)
        blit_text(
            surface,
            self.font(8),
            "Drift is simulated. There is no magnetometer in this device.",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 10),
        )

    def _draw_dial(self, surface, pal, center: tuple[int, int], radius: int) -> None:
        cx, cy = center
        pygame.draw.circle(surface, pal.surface, center, radius)
        pygame.draw.circle(surface, pal.stroke, center, radius, 1)
        pygame.draw.circle(surface, pal.surface_alt, center, int(radius * 0.78))
        for tick in range(72):
            angle = tick * math.tau / 72 - math.pi / 2
            major = tick % 6 == 0
            inner = radius * (0.88 if major else 0.93)
            outer = radius * 0.97
            start = (cx + math.cos(angle) * inner, cy + math.sin(angle) * inner)
            end = (cx + math.cos(angle) * outer, cy + math.sin(angle) * outer)
            pygame.draw.line(surface, pal.text_secondary if major else pal.text_muted, start, end, 2 if major else 1)
        for angle, label in ((-90, "N"), (-45, "NE"), (0, "E"), (45, "SE"), (90, "S"), (135, "SW"), (180, "W"), (225, "NW")):
            radians = math.radians(angle)
            x = cx + math.cos(radians) * radius * 0.80
            y = cy + math.sin(radians) * radius * 0.80
            blit_text(
                surface,
                self.font(11 if label in ("N", "E", "S", "W") else 8, True),
                label,
                pal.danger if label == "N" else pal.text_secondary,
                center=(x, y),
            )
        angle = math.radians(self._heading - 90.0)
        tip = (cx + math.cos(angle) * radius * 0.62, cy + math.sin(angle) * radius * 0.62)
        tail = (cx - math.cos(angle) * radius * 0.62, cy - math.sin(angle) * radius * 0.62)
        side = (cx + math.cos(angle + math.pi / 2) * radius * 0.09, cy + math.sin(angle + math.pi / 2) * radius * 0.09)
        side2 = (cx + math.cos(angle - math.pi / 2) * radius * 0.09, cy + math.sin(angle - math.pi / 2) * radius * 0.09)
        pygame.draw.polygon(surface, pal.danger, [tip, side, (cx, cy), side2])
        pygame.draw.polygon(surface, pal.text_secondary, [tail, side, (cx, cy), side2])
        pygame.draw.circle(surface, pal.surface_raised, center, max(4, radius // 12))
        pygame.draw.circle(surface, pal.text, center, max(2, radius // 28))
        if self._calibrating > 0.0:
            pulse = 0.5 + 0.5 * math.sin(self._phase * 18.0)
            pygame.draw.circle(surface, with_alpha(pal.warning, int(90 + 100 * pulse)), center, radius + 4, 3)

    def _draw_readout(self, surface, pal, center_x: int, center_y: int) -> None:
        blit_text(
            surface,
            self.mono(44, True),
            f"{self._heading:03.0f}\u00b0",
            pal.text,
            center=(center_x, center_y),
        )
        blit_text(
            surface,
            self.font(13, True),
            self._cardinal(),
            pal.accent_ink,
            center=(center_x, center_y + 38),
            tracking=2.0,
        )
        bubble_size = 84
        bubble = pygame.Rect(center_x - bubble_size // 2, center_y + 72, bubble_size, bubble_size)
        pygame.draw.circle(surface, pal.surface, bubble.center, bubble_size // 2)
        pygame.draw.circle(surface, pal.stroke_soft, bubble.center, bubble_size // 2, 1)
        pygame.draw.circle(surface, pal.stroke_soft, bubble.center, bubble_size // 4, 1)
        bubble_x = bubble.centerx + int(self._bubble[0] * bubble_size * 0.30)
        bubble_y = bubble.centery + int(self._bubble[1] * bubble_size * 0.30)
        pygame.draw.circle(surface, pal.success, (bubble_x, bubble_y), max(6, bubble_size // 9))
        blit_text(surface, self.font(8, True), "LEVEL", pal.text_muted, center=(bubble.centerx, bubble.bottom + 12), tracking=1.4)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["CompassApp", "draw_icon"]
