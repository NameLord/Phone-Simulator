"""Fullscreen alarm and timer-finished overlay."""

from __future__ import annotations

import math

import pygame

from core.input import EventKind, PointerEvent
from ui.layout import layout
from ui.paint import blit_text, draw_rounded, with_alpha


class AlarmOverlay:
    """Driven by ``services/schedules.py``; sits above every scene."""

    def __init__(self, schedules, theme) -> None:
        self._schedules = schedules
        self._theme = theme
        self.phase = 0.0

    @property
    def is_open(self) -> bool:
        return self._schedules.active_event is not None

    def update(self, dt: float) -> None:
        self.phase = (self.phase + max(0.0, dt)) % 2.0

    # -- geometry -------------------------------------------------------------
    def _panel(self) -> pygame.Rect:
        width = min(440, layout.width - layout.margin * 2)
        height = min(520, layout.height - layout.margin * 2)
        return pygame.Rect((layout.width - width) // 2, (layout.height - height) // 2, width, height)

    def _snooze_rect(self) -> pygame.Rect:
        panel = self._panel()
        width = (panel.w - 48) // 2
        return pygame.Rect(panel.x + 16, panel.bottom - 70, width, 48)

    def _dismiss_rect(self) -> pygame.Rect:
        panel = self._panel()
        width = (panel.w - 48) // 2
        return pygame.Rect(panel.x + 32 + width, panel.bottom - 70, width, 48)

    def _timer_dismiss_rect(self) -> pygame.Rect:
        panel = self._panel()
        return pygame.Rect(panel.x, panel.bottom - 70, panel.w, 48)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        event_data = self._schedules.active_event or {}
        kind = str(event_data.get("kind", "alarm"))
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._schedules.dismiss()
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if kind == "alarm":
                    self._schedules.snooze()
                else:
                    self._schedules.dismiss()
            return True
        if event.kind is EventKind.TAP:
            if kind == "alarm" and self._snooze_rect().collidepoint(event.pos):
                self._schedules.snooze()
                return True
            dismiss = self._dismiss_rect() if kind == "alarm" else self._timer_dismiss_rect()
            if dismiss.collidepoint(event.pos):
                self._schedules.dismiss()
                return True
        return event.kind is EventKind.POINTER_DOWN

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        event_data = self._schedules.active_event
        if event_data is None:
            return
        pal = self._theme.palette
        kind = str(event_data.get("kind", "alarm"))
        surface.fill(pal.bg_bottom)
        halo = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        pulse = 0.5 + 0.5 * math.sin(self.phase * math.tau)
        pygame.draw.circle(
            halo,
            with_alpha(pal.warning if kind == "alarm" else pal.success, int(28 + 22 * pulse)),
            (layout.center_x, int(layout.height * 0.28)),
            int(min(layout.width, layout.height) * 0.34),
        )
        surface.blit(halo, (0, 0))

        blit_text(
            surface,
            self._theme.font(11, True),
            "ALARM" if kind == "alarm" else "TIMER FINISHED",
            pal.warning if kind == "alarm" else pal.success,
            center=(layout.center_x, int(layout.height * 0.18)),
            tracking=3.0,
        )
        blit_text(
            surface,
            self._theme.mono(54, True),
            self._clock_time(),
            pal.text,
            center=(layout.center_x, int(layout.height * 0.27)),
        )
        self._draw_bell(surface, (layout.center_x, int(layout.height * 0.41)), pal.warning if kind == "alarm" else pal.success)
        blit_text(
            surface,
            self._theme.font(24, True),
            str(event_data.get("title", "Alarm"))[:40],
            pal.text,
            center=(layout.center_x, int(layout.height * 0.55)),
        )
        blit_text(
            surface,
            self._theme.font(11),
            f"Scheduled for {event_data.get('time', '--:--')}" if kind == "alarm" else "Your countdown is complete",
            pal.text_muted,
            center=(layout.center_x, int(layout.height * 0.60)),
        )

        if kind == "alarm":
            snooze = self._snooze_rect()
            dismiss = self._dismiss_rect()
            draw_rounded(surface, snooze, fill=pal.surface_raised, stroke=pal.stroke, radius=24)
            blit_text(surface, self._theme.font(12, True), "SNOOZE 5 MIN", pal.text_secondary, center=snooze.center, tracking=0.8)
            draw_rounded(surface, dismiss, fill=pal.warning, radius=24)
            blit_text(surface, self._theme.font(12, True), "DISMISS", pal.on_accent, center=dismiss.center, tracking=1.0)
        else:
            dismiss = self._timer_dismiss_rect()
            draw_rounded(surface, dismiss, fill=pal.success, radius=24)
            blit_text(surface, self._theme.font(12, True), "DISMISS", pal.on_accent, center=dismiss.center, tracking=1.0)

    def _clock_time(self) -> str:
        clock = getattr(self._schedules, "clock", None)
        if clock is None:
            return "--:--"
        try:
            return clock.time_string()
        except Exception:  # noqa: BLE001 - overlay must still render
            return "--:--"

    def _draw_bell(self, surface: pygame.Surface, center: tuple[int, int], color) -> None:
        cx, cy = center
        pulse = 1.0 + 0.05 * math.sin(self.phase * math.tau * 2.0)
        width = int(74 * pulse)
        height = int(58 * pulse)
        rect = pygame.Rect(cx - width // 2, cy - height // 2, width, height)
        pygame.draw.arc(surface, color, rect, math.pi, math.tau, 4)
        pygame.draw.line(surface, color, (rect.left, rect.centery), (rect.left, rect.bottom), 4)
        pygame.draw.line(surface, color, (rect.right, rect.centery), (rect.right, rect.bottom), 4)
        pygame.draw.line(surface, color, (rect.left - 8, rect.bottom), (rect.right + 8, rect.bottom), 4)
        pygame.draw.circle(surface, color, (cx, rect.bottom + 10), 6)
        for index in range(2):
            side = -1 if index == 0 else 1
            y = cy - 8 + index * 12
            pygame.draw.line(surface, color, (cx + side * (width // 2 + 8), y), (cx + side * (width // 2 + 20), y + 4), 3)
