"""Fullscreen incoming/active call overlay driven by services/telephony.py."""

from __future__ import annotations

import math

import pygame

from core.input import EventKind, PointerEvent
from core.scene import clamp
from ui.layout import layout
from ui.paint import blit_text, draw_rounded, with_alpha


class CallOverlay:
    """Ring animation, Accept/Decline and live call timer."""

    def __init__(self, telephony, theme) -> None:
        self._telephony = telephony
        self._theme = theme
        self.phase = 0.0

    @property
    def is_open(self) -> bool:
        return self._telephony.active_call is not None

    def update(self, dt: float) -> None:
        self.phase = (self.phase + max(0.0, dt)) % 2.0

    # -- geometry -------------------------------------------------------------
    def _accept_rect(self) -> pygame.Rect:
        width = min(190, (layout.width - layout.margin * 4) // 2)
        y = int(layout.height * 0.73)
        return pygame.Rect(
            layout.center_x + layout.margin,
            y,
            width,
            58,
        )

    def _decline_rect(self) -> pygame.Rect:
        width = min(190, (layout.width - layout.margin * 4) // 2)
        y = int(layout.height * 0.73)
        return pygame.Rect(
            layout.center_x - layout.margin - width,
            y,
            width,
            58,
        )

    def _end_rect(self) -> pygame.Rect:
        width = min(220, layout.width - layout.margin * 4)
        return pygame.Rect(layout.center_x - width // 2, int(layout.height * 0.73), width, 58)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        call = self._telephony.active_call
        if call is None:
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                if call.is_ringing:
                    self._telephony.decline_call()
                else:
                    self._telephony.end_call()
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                if call.is_ringing:
                    self._telephony.accept_call()
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        if call.is_ringing:
            if self._accept_rect().collidepoint(event.pos):
                self._telephony.accept_call()
                return True
            if self._decline_rect().collidepoint(event.pos):
                self._telephony.decline_call()
                return True
        elif self._end_rect().collidepoint(event.pos):
            self._telephony.end_call()
            return True
        return True

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        call = self._telephony.active_call
        if call is None:
            return
        pal = self._theme.palette
        surface.fill(pal.bg_bottom)
        glow = pygame.Surface((surface.get_width(), surface.get_height()), pygame.SRCALPHA)
        radius = int(min(surface.get_width(), surface.get_height()) * (0.26 + 0.025 * math.sin(self.phase * math.tau)))
        pygame.draw.circle(
            glow,
            with_alpha(pal.accent, 26),
            (layout.center_x, int(layout.height * 0.30)),
            radius,
        )
        surface.blit(glow, (0, 0))

        center = (layout.center_x, int(layout.height * 0.30))
        avatar_radius = int(min(layout.width, layout.height) * 0.075)
        contact = self._telephony.contact(call.contact_id)
        color = contact.color if contact is not None else pal.accent
        pygame.draw.circle(surface, with_alpha(color, 46), center, avatar_radius + 12)
        pygame.draw.circle(surface, color, center, avatar_radius)
        blit_text(
            surface,
            self._theme.font(max(24, int(avatar_radius * 0.8)), True),
            call.name[:1].upper(),
            pal.on_accent,
            center=center,
        )

        blit_text(
            surface,
            self._theme.font(12),
            "INCOMING CALL" if call.is_ringing else "ON CALL",
            pal.text_muted,
            center=(layout.center_x, int(layout.height * 0.42)),
            tracking=3.0,
        )
        blit_text(
            surface,
            self._theme.font(28, True),
            call.name,
            pal.text,
            center=(layout.center_x, int(layout.height * 0.48)),
        )
        blit_text(
            surface,
            self._theme.font(13),
            call.number,
            pal.text_secondary,
            center=(layout.center_x, int(layout.height * 0.54)),
        )
        if call.is_ringing:
            pulse = 0.5 + 0.5 * math.sin(self.phase * math.tau * 2.0)
            blit_text(
                surface,
                self._theme.font(12),
                "ringing\u2026",
                pal.accent_ink,
                center=(layout.center_x, int(layout.height * 0.60)),
                alpha=int(150 + 100 * pulse),
            )
            accept = self._accept_rect()
            decline = self._decline_rect()
            draw_rounded(surface, accept, fill=pal.success, radius=28)
            blit_text(surface, self._theme.font(14, True), "ACCEPT", pal.on_accent, center=accept.center, tracking=1.2)
            draw_rounded(surface, decline, fill=pal.danger, radius=28)
            blit_text(surface, self._theme.font(14, True), "DECLINE", pal.on_accent, center=decline.center, tracking=1.2)
        else:
            duration = int(self._telephony.call_duration())
            minutes, seconds = divmod(duration, 60)
            blit_text(
                surface,
                self._theme.mono(22, True),
                f"{minutes:02d}:{seconds:02d}",
                pal.text,
                center=(layout.center_x, int(layout.height * 0.61)),
            )
            end = self._end_rect()
            draw_rounded(surface, end, fill=pal.danger, radius=28)
            blit_text(surface, self._theme.font(14, True), "END CALL", pal.on_accent, center=end.center, tracking=1.2)
