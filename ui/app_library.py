"""Alphabetical app library overlay, independent of home pages/folders."""

from __future__ import annotations

import pygame

from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text


class AppLibrary:
    def __init__(self, scene) -> None:
        self.scene = scene
        self.is_open = False
        self.query = ""
        self._rows: list[tuple[str, pygame.Rect]] = []
        self._close_rect = pygame.Rect(0, 0, 0, 0)

    def open(self) -> None:
        self.is_open = True
        self.query = ""

    def close(self) -> None:
        self.is_open = False

    def entries(self):
        manager = self.scene.registry.require("app_manager")
        values = [manifest for manifest in manager.installed_manifests()]
        query = self.query.lower().strip()
        if query:
            values = [item for item in values if query in item.name.lower() or query in item.app_id.lower()]
        return sorted(values, key=lambda item: item.name.lower())

    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            elif event.key == pygame.K_BACKSPACE:
                self.query = self.query[:-1]
            elif event.text and event.text.isprintable():
                self.query = (self.query + event.text)[:32]
            return True
        if event.kind is EventKind.TAP:
            if self._close_rect.collidepoint(event.pos):
                self.close()
                return True
            for app_id, rect in self._rows:
                if rect.collidepoint(event.pos):
                    self.close()
                    manager = self.scene.registry.require("app_manager")
                    if manager.launch(app_id) is not None:
                        self.scene.go("app", fade=0.14)
                    return True
            # Tap the scrim closes.
            self.close()
            return True
        return True

    def draw(self, surface: pygame.Surface) -> None:
        if not self.is_open:
            return
        pal = self.scene.theme.palette
        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill((*pal.scrim, 200))
        surface.blit(scrim, (0, 0))
        margin = current_layout.margin
        top = current_layout.status_bar_height + 16
        blit_text(surface, self.scene.font(26, True), "App Library", pal.text, topleft=(margin, top))
        self._close_rect = pygame.Rect(current_layout.width - margin - 74, top, 74, 34)
        draw_rounded(surface, self._close_rect, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=12)
        blit_text(surface, self.scene.font(10, True), "CLOSE", pal.text_secondary, center=self._close_rect.center, tracking=1)
        field = pygame.Rect(margin, top + 48, current_layout.width - margin * 2, 48)
        draw_rounded(surface, field, fill=pal.surface, stroke=pal.accent if self.query else pal.stroke_soft, radius=16)
        font = self.scene.font(15)
        blit_text(surface, font, fit_text(font, self.query or "Search all installed apps", field.w - 28), pal.text if self.query else pal.text_muted, midleft=(field.x + 16, field.centery))
        entries = self.entries()
        columns = 4 if current_layout.is_landscape else 3
        gap = 16
        cell_w = (current_layout.width - margin * 2 - gap * (columns - 1)) // columns
        cell_h = 118
        y = field.bottom + 22
        self._rows = []
        for index, manifest in enumerate(entries):
            row, col = divmod(index, columns)
            cell = pygame.Rect(margin + col * (cell_w + gap), y + row * (cell_h + gap), cell_w, cell_h)
            if cell.bottom > current_layout.height - 20:
                break
            draw_rounded(surface, cell, fill=pal.surface, stroke=pal.stroke_soft, radius=22, alpha=230)
            icon = pygame.Rect(0, 0, 56, 56)
            icon.midtop = (cell.centerx, cell.y + 14)
            manifest.icon(surface, icon, self.scene.theme)
            label_font = self.scene.font(11, True)
            blit_text(surface, label_font, fit_text(label_font, manifest.name, cell.w - 16), pal.text, center=(cell.centerx, cell.bottom - 20))
            self._rows.append((manifest.app_id, cell))
        if not entries:
            blit_text(surface, self.scene.font(14), "No apps match", pal.text_muted, center=(current_layout.center_x, field.bottom + 80))
        blit_text(surface, self.scene.font(10), "Swipe up from the dock or press Esc to close", pal.text_muted, center=(current_layout.center_x, current_layout.height - 14))
