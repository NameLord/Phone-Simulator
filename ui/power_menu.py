"""Power menu and critical-battery dialog."""

from __future__ import annotations

from typing import Callable

import pygame

from core.input import EventKind, PointerEvent
from core.scene import clamp, ease_out_cubic
from ui.layout import layout
from ui.paint import blit_text, draw_chevron, draw_rounded, with_alpha


class PowerMenu:
    """Slide-to-power-off sheet opened by a long press on the power glyph."""

    def __init__(
        self,
        on_shutdown: Callable[[], None],
        on_restart: Callable[[], None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._on_shutdown = on_shutdown
        self._on_restart = on_restart
        self._on_close = on_close
        self.is_open = False
        self._opening = 0.0
        self.slide = 0.0
        self._dragging = False

    def open(self) -> None:
        self.is_open = True
        self._opening = 0.0
        self.slide = 0.0
        self._dragging = False

    def close(self) -> None:
        self.is_open = False
        self.slide = 0.0
        self._dragging = False
        if self._on_close is not None:
            self._on_close()

    def update(self, dt: float) -> None:
        if not self.is_open:
            return
        self._opening = min(1.0, self._opening + max(0.0, dt) * 6.0)

    # -- geometry -------------------------------------------------------------
    def _panel_rect(self) -> pygame.Rect:
        width = min(460, layout.width - layout.margin * 2)
        height = min(330, layout.height - layout.margin * 2)
        return pygame.Rect(
            (layout.width - width) // 2,
            layout.height - height - max(18, layout.bottom),
            width,
            height,
        )

    def _track_rect(self) -> pygame.Rect:
        panel = self._panel_rect()
        return pygame.Rect(panel.x + 26, panel.bottom - 92, panel.w - 52, 58)

    def _restart_rect(self) -> pygame.Rect:
        panel = self._panel_rect()
        return pygame.Rect(panel.x + 26, panel.bottom - 174, panel.w - 52, 52)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            return True
        panel = self._panel_rect()
        if event.kind is EventKind.TAP:
            if self._restart_rect().collidepoint(event.pos):
                self.close()
                self._on_restart()
                return True
            if not panel.collidepoint(event.pos):
                self.close()
                return True
            if self._track_rect().collidepoint(event.pos):
                # A tap at the far end is an intentional confirm.
                track = self._track_rect()
                self.slide = clamp((event.pos[0] - track.x) / max(1, track.w), 0.0, 1.0)
                if self.slide > 0.9:
                    self.close()
                    self._on_shutdown()
                return True
            return True
        if event.kind is EventKind.POINTER_DOWN:
            if self._track_rect().collidepoint(event.pos):
                self._dragging = True
                return True
            return panel.collidepoint(event.pos)
        if event.kind is EventKind.DRAG and self._dragging:
            track = self._track_rect()
            self.slide = clamp((event.pos[0] - track.x) / max(1, track.w), 0.0, 1.0)
            return True
        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END) and self._dragging:
            self._dragging = False
            if self.slide >= 0.9:
                self.close()
                self._on_shutdown()
            else:
                self.slide = 0.0
            return True
        return True

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface, theme) -> None:
        if not self.is_open:
            return
        pal = theme.palette
        alpha = int(255 * ease_out_cubic(self._opening))
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill(with_alpha(pal.scrim, int(170 * self._opening)))
        surface.blit(scrim, (0, 0))

        panel = self._panel_rect().move(0, int((1.0 - self._opening) * 40))
        draw_rounded(surface, panel, fill=pal.surface_raised, stroke=pal.stroke, radius=24, alpha=alpha)
        blit_text(
            surface,
            theme.font(20, True),
            "Power",
            pal.text,
            topleft=(panel.x + 26, panel.y + 22),
            alpha=alpha,
        )
        blit_text(
            surface,
            theme.font(11),
            "Restart or slide to power off",
            pal.text_muted,
            topleft=(panel.x + 28, panel.y + 50),
            alpha=alpha,
        )

        restart = self._restart_rect().move(0, panel.y - self._panel_rect().y)
        draw_rounded(surface, restart, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14, alpha=alpha)
        blit_text(surface, theme.font(14, True), "Restart", pal.text, center=restart.center, alpha=alpha)

        track = self._track_rect().move(0, panel.y - self._panel_rect().y)
        draw_rounded(surface, track, fill=pal.surface, stroke=pal.stroke_soft, radius=29, alpha=alpha)
        fill_w = max(4, int(track.w * self.slide))
        draw_rounded(
            surface,
            pygame.Rect(track.x, track.y, fill_w, track.h),
            fill=pal.danger if self.slide > 0.75 else pal.surface_alt,
            radius=29,
            alpha=alpha,
        )
        knob_x = track.x + 29 + int((track.w - 58) * self.slide)
        pygame.draw.circle(surface, pal.surface_raised, (knob_x, track.centery), 23)
        pygame.draw.circle(surface, pal.danger if self.slide > 0.75 else pal.stroke, (knob_x, track.centery), 23, 2)
        draw_chevron(surface, (knob_x, track.centery), pal.text, size=6, thickness=2)
        blit_text(
            surface,
            theme.font(11, True),
            "SLIDE TO POWER OFF",
            pal.text_muted,
            center=(track.centerx + 20, track.centery),
            alpha=alpha,
            tracking=1.2,
        )


class BatteryAlert:
    """Red modal for the 5% battery threshold."""

    def __init__(self, power) -> None:
        self._power = power

    @property
    def is_open(self) -> bool:
        return bool(self._power.critical_alert)

    def _rect(self) -> pygame.Rect:
        width = min(420, layout.width - layout.margin * 2)
        height = min(240, layout.height - layout.margin * 2)
        return pygame.Rect(
            (layout.width - width) // 2,
            (layout.height - height) // 2,
            width,
            height,
        )

    def _buttons(self) -> tuple[pygame.Rect, pygame.Rect]:
        rect = self._rect()
        gap = 10
        width = (rect.w - 52 - gap) // 2
        y = rect.bottom - 64
        return (
            pygame.Rect(rect.x + 26, y, width, 40),
            pygame.Rect(rect.x + 26 + width + gap, y, width, 40),
        )

    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._power.acknowledge_critical()
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._power.plug_in()
                self._power.acknowledge_critical()
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        plug, dismiss = self._buttons()
        if plug.collidepoint(event.pos):
            self._power.plug_in()
            self._power.acknowledge_critical()
            return True
        if dismiss.collidepoint(event.pos):
            self._power.acknowledge_critical()
            return True
        return True

    def draw(self, surface: pygame.Surface, theme) -> None:
        if not self.is_open:
            return
        pal = theme.palette
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill(with_alpha(pal.scrim, 178))
        surface.blit(scrim, (0, 0))
        rect = self._rect()
        draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.danger, width=2, radius=24)
        pygame.draw.circle(surface, pal.danger, (rect.x + 42, rect.y + 42), 17)
        blit_text(surface, theme.font(20, True), "!", pal.on_accent, center=(rect.x + 42, rect.y + 40))
        blit_text(
            surface,
            theme.font(18, True),
            "Battery critically low",
            pal.danger,
            topleft=(rect.x + 70, rect.y + 28),
        )
        blit_text(
            surface,
            theme.font(12),
            f"{self._power.percent:.0f}% remaining. The device will sleep at 0%.",
            pal.text_secondary,
            topleft=(rect.x + 70, rect.y + 56),
        )
        plug, dismiss = self._buttons()
        draw_rounded(surface, plug, fill=pal.danger, radius=14)
        blit_text(surface, theme.font(12, True), "Plug in", pal.on_accent, center=plug.center)
        draw_rounded(surface, dismiss, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, theme.font(12, True), "Dismiss", pal.text, center=dismiss.center)
