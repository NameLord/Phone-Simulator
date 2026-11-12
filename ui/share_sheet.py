"""Bottom share sheet shared by every app."""

from __future__ import annotations

import pygame

from core.input import EventKind, PointerEvent
from ui.paint import blit_text, draw_rounded, fit_text


class ShareSheet:
    def __init__(self, service, apps, theme) -> None:
        self.service = service
        self.apps = apps
        self.theme = theme
        self.is_open = False
        self._rows: list[tuple[str, pygame.Rect]] = []
        self._cancel = pygame.Rect(0, 0, 0, 0)

    def open(self, payload: dict) -> bool:
        if not self.service.open(payload):
            return False
        self.is_open = True
        return True

    def close(self) -> None:
        self.is_open = False
        self.service.close()

    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self.close()
            return True
        if event.kind is EventKind.TAP:
            if self._cancel.collidepoint(event.pos):
                self.close()
                return True
            for app_id, rect in self._rows:
                if rect.collidepoint(event.pos):
                    if self.service.handoff(app_id):
                        self.is_open = False
                    return True
            # Tapping the scrim dismisses the sheet.
            self.close()
            return True
        return True

    def draw(self, surface: pygame.Surface) -> None:
        if not self.is_open or self.service.payload is None:
            return
        theme = self.theme
        pal = theme.palette
        width = min(surface.get_width() - 24, 520)
        payload = self.service.payload
        candidates = self.service.candidates()
        height = 132 + 66 * max(1, len(candidates))
        rect = pygame.Rect((surface.get_width() - width) // 2, surface.get_height() - height - 12, width, height)
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((*pal.scrim, 150))
        surface.blit(scrim, (0, 0))
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=28, alpha=250)
        blit_text(surface, theme.font(17, True), "Share", pal.text, topleft=(rect.x + 22, rect.y + 18))
        kind = str(payload.get("type", "")).upper()
        preview = str(payload.get("text") or payload.get("title") or payload.get("path") or kind)
        blit_text(surface, theme.font(11), fit_text(theme.font(11), f"{kind} \u00b7 {preview}", rect.w - 44), pal.text_muted, topleft=(rect.x + 22, rect.y + 44))
        self._rows = []
        y = rect.y + 78
        for app_id, name in candidates:
            row = pygame.Rect(rect.x + 14, y, rect.w - 28, 58)
            draw_rounded(surface, row, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=16)
            manifest = self.apps.manifest(app_id)
            icon = pygame.Rect(row.x + 12, row.y + 9, 40, 40)
            if manifest is not None:
                manifest.icon(surface, icon, theme)
            blit_text(surface, theme.font(14, True), name, pal.text, midleft=(icon.right + 14, row.centery - 7))
            blit_text(surface, theme.font(9), "ACCEPTS " + kind, pal.text_muted, midleft=(icon.right + 14, row.centery + 10), tracking=1)
            self._rows.append((app_id, row))
            y += 66
        if not candidates:
            blit_text(surface, theme.font(13), "No installed app accepts this item.", pal.text_muted, center=(rect.centerx, rect.y + 112))
        self._cancel = pygame.Rect(rect.centerx - 60, rect.bottom - 52, 120, 38)
        draw_rounded(surface, self._cancel, fill=pal.surface_raised, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, theme.font(12, True), "Cancel", pal.text_secondary, center=self._cancel.center)
