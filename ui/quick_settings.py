"""Quick settings grid drawn inside the notification shade."""

from __future__ import annotations

import pygame

from core.input import EventKind
from ui.paint import blit_text, draw_rounded, fit_text
from ui.theme import Theme


ICON_KINDS = {
    "wifi": "wifi",
    "bluetooth": "bt",
    "dnd": "moon",
    "battery_saver": "battery",
    "flashlight": "flash",
    "airplane": "plane",
    "rotation": "rotate",
    "dark_mode": "moon",
    "recording": "record",
    "hotspot": "wifi",
    "brightness": "sun",
    "volume": "sound",
}


class QuickSettingsPanel:
    def __init__(self, service, theme: Theme) -> None:
        self.service = service
        self.theme = theme
        self.editing = False
        self._slider: str | None = None
        self._moving: str | None = None
        self._rect = pygame.Rect(0, 0, 0, 0)
        self._tile_rects: list[tuple[str, pygame.Rect]] = []
        self._edit_rect = pygame.Rect(0, 0, 0, 0)
        self._add_rects: list[tuple[str, pygame.Rect]] = []

    # -- layout --------------------------------------------------------------
    def _layout(self, rect: pygame.Rect) -> None:
        self._rect = rect
        self._tile_rects = []
        columns = 4 if rect.w >= 720 else 2
        gap = 10
        tile_w = (rect.w - gap * (columns - 1)) / columns
        tile_h = 72
        for index, tile in enumerate(self.service.tiles()):
            row, col = divmod(index, columns)
            self._tile_rects.append(
                (
                    tile.tile_id,
                    pygame.Rect(
                        int(rect.x + col * (tile_w + gap)),
                        int(rect.y + 42 + row * (tile_h + gap)),
                        int(tile_w),
                        tile_h,
                    ),
                )
            )
        self._edit_rect = pygame.Rect(rect.right - 88, rect.y + 6, 88, 28)

    def _add_layout(self) -> None:
        self._add_rects = []
        if not self.editing:
            return
        available = [tile for tile in self.service.all_tiles if tile.tile_id not in {t for t, _ in self._tile_rects}]
        y = self._rect.bottom - 54
        x = self._rect.x
        for tile in available:
            rect = pygame.Rect(x, y, 108, 32)
            if rect.right > self._rect.right:
                break
            self._add_rects.append((tile.tile_id, rect))
            x += 114

    # -- input ---------------------------------------------------------------
    def handle(self, event, panel_y: int) -> bool:
        rect = pygame.Rect(24, panel_y + 146, max(200, self._rect.w), max(200, self._rect.h))
        # The caller updates the rect through draw; keep the last one if valid.
        if self._rect.w <= 0:
            self._rect = rect
        if event.kind is EventKind.POINTER_DOWN:
            if self.editing:
                for tile_id, tile_rect in self._tile_rects:
                    if tile_rect.collidepoint(event.pos):
                        self._moving = tile_id
                        return True
            for tile_id, tile_rect in self._tile_rects:
                if tile_rect.collidepoint(event.pos) and tile_id in ("brightness", "volume"):
                    self._slider = tile_id
                    self._set_slider(tile_rect, event.pos[0])
                    return True
            return False
        if event.kind is EventKind.DRAG:
            if self._moving is not None:
                target = self._tile_index_at(event.pos)
                if target is not None:
                    current = [tile.tile_id for tile in self.service.tiles()].index(self._moving) if self._moving in [tile.tile_id for tile in self.service.tiles()] else -1
                    if current >= 0 and target != current:
                        self.service.move(self._moving, target)
                return True
            if self._slider is not None:
                for tile_id, tile_rect in self._tile_rects:
                    if tile_id == self._slider:
                        self._set_slider(tile_rect, event.pos[0])
                        return True
        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            self._slider = None
            self._moving = None
        if event.kind is not EventKind.TAP:
            return False
        if self._edit_rect.collidepoint(event.pos):
            self.editing = not self.editing
            return True
        if self.editing:
            for tile_id, rect in self._add_rects:
                if rect.collidepoint(event.pos):
                    self.service.add(tile_id)
                    return True
        for tile_id, rect in self._tile_rects:
            if not rect.collidepoint(event.pos):
                continue
            if self.editing and self.service.can_remove(tile_id):
                self.service.remove(tile_id)
            else:
                self.service.activate(tile_id)
            return True
        return False

    def _set_slider(self, rect: pygame.Rect, x: float) -> None:
        if self._slider is None:
            return
        track = pygame.Rect(rect.x + 12, rect.bottom - 18, rect.w - 24, 5)
        self.service.set_value(self._slider, (x - track.x) / max(1, track.w))

    def _tile_index_at(self, pos: tuple[float, float]) -> int | None:
        for index, (_tile_id, rect) in enumerate(self._tile_rects):
            if rect.collidepoint(pos):
                return index
        return None

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface, rect: pygame.Rect, alpha: int = 255) -> None:
        self._layout(rect)
        pal = self.theme.palette
        draw_rounded(surface, rect, fill=pal.shade, radius=24, alpha=alpha)
        title_font = self.theme.font(17, True)
        blit_text(surface, title_font, "Quick settings", pal.text, topleft=(rect.x, rect.y + 4), alpha=alpha)
        edit_font = self.theme.font(10, True)
        blit_text(surface, edit_font, "DONE" if self.editing else "EDIT", pal.accent_ink, center=self._edit_rect.center, alpha=alpha, tracking=1)
        if self.editing:
            blit_text(surface, self.theme.font(10), "Tap a tile to remove it", pal.text_muted, topleft=(rect.x + 150, rect.y + 10), alpha=alpha)

        for tile_id, tile_rect in self._tile_rects:
            tile = next((item for item in self.service.all_tiles if item.tile_id == tile_id), None)
            if tile is None:
                continue
            enabled = self.service.enabled(tile_id)
            if tile.kind == "slider":
                active = True
                fill = pal.surface_alt
            else:
                active = enabled
                fill = pal.accent_soft if enabled else pal.surface_alt
            draw_rounded(surface, tile_rect, fill=fill, stroke=pal.accent if active else pal.stroke_soft, radius=20, alpha=alpha)
            self._draw_icon(surface, ICON_KINDS.get(tile_id, "dot"), (tile_rect.x + 20, tile_rect.y + 22), pal.accent_ink if active else pal.text_secondary, alpha)
            label_font = self.theme.font(11, True)
            blit_text(surface, label_font, fit_text(label_font, tile.label, tile_rect.w - 38), pal.text, topleft=(tile_rect.x + 36, tile_rect.y + 12), alpha=alpha)
            blit_text(surface, self.theme.font(9), fit_text(self.theme.font(9), self.service.subtitle(tile_id), tile_rect.w - 24), pal.text_secondary, topleft=(tile_rect.x + 12, tile_rect.y + 34), alpha=alpha)
            if tile.kind == "slider":
                track = pygame.Rect(tile_rect.x + 12, tile_rect.bottom - 18, tile_rect.w - 24, 5)
                draw_rounded(surface, track, fill=pal.track, radius=2, alpha=alpha)
                value = self.service.value(tile_id)
                draw_rounded(surface, pygame.Rect(track.x, track.y, max(2, int(track.w * value)), track.h), fill=pal.accent, radius=2, alpha=alpha)
            if self.editing and self.service.can_remove(tile_id):
                pygame.draw.circle(surface, pal.danger, (tile_rect.right - 14, tile_rect.y + 14), 8)
                pygame.draw.line(surface, pal.on_accent, (tile_rect.right - 18, tile_rect.y + 10), (tile_rect.right - 10, tile_rect.y + 18), 1)
                pygame.draw.line(surface, pal.on_accent, (tile_rect.right - 10, tile_rect.y + 10), (tile_rect.right - 18, tile_rect.y + 18), 1)

        self._add_layout()
        if self.editing:
            blit_text(surface, self.theme.font(9, True), "ADD", pal.text_muted, topleft=(rect.x, rect.bottom - 72), alpha=alpha, tracking=1)
            for tile_id, add_rect in self._add_rects:
                draw_rounded(surface, add_rect, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14, alpha=alpha)
                blit_text(surface, self.theme.font(9), tile_id.replace("_", " ").title(), pal.text_secondary, center=add_rect.center, alpha=alpha)

    def _draw_icon(self, surface, kind: str, center: tuple[int, int], color, alpha: int) -> None:
        cx, cy = center
        if kind == "wifi":
            for radius in (4, 9, 14):
                pygame.draw.arc(surface, color, (cx - radius, cy + 6 - radius, radius * 2, radius * 2), 0.6, 2.54, 2)
        elif kind == "bt":
            pygame.draw.line(surface, color, (cx, cy - 10), (cx, cy + 10), 2)
            pygame.draw.lines(surface, color, False, [(cx, cy - 10), (cx + 6, cy - 4), (cx - 6, cy + 4), (cx, cy + 10)], 2)
        elif kind == "moon":
            pygame.draw.circle(surface, color, center, 9, 2)
            pygame.draw.circle(surface, self.theme.palette.surface_alt, (cx + 5, cy - 3), 8)
        elif kind == "battery":
            pygame.draw.rect(surface, color, (cx - 11, cy - 6, 18, 12), 2, border_radius=3)
            pygame.draw.rect(surface, color, (cx + 8, cy - 2, 3, 4))
            pygame.draw.rect(surface, color, (cx - 8, cy - 3, 12, 6))
        elif kind == "flash":
            pygame.draw.polygon(surface, color, [(cx + 2, cy - 11), (cx - 7, cy + 2), (cx - 1, cy + 1), (cx - 3, cy + 11), (cx + 7, cy - 2), (cx + 1, cy - 1)])
        elif kind == "plane":
            pygame.draw.line(surface, color, (cx - 12, cy + 6), (cx + 12, cy - 6), 3)
            pygame.draw.line(surface, color, (cx - 2, cy - 2), (cx + 4, cy + 10), 2)
            pygame.draw.line(surface, color, (cx + 2, cy + 2), (cx - 4, cy - 8), 2)
        elif kind == "rotate":
            pygame.draw.arc(surface, color, (cx - 10, cy - 10, 20, 20), 0.4, 5.2, 2)
            pygame.draw.polygon(surface, color, [(cx + 8, cy - 8), (cx + 12, cy - 2), (cx + 4, cy - 2)])
        elif kind == "record":
            pygame.draw.circle(surface, self.theme.palette.danger, center, 8)
        elif kind == "sun":
            pygame.draw.circle(surface, color, center, 6)
            for index in range(8):
                import math

                angle = index * math.tau / 8
                pygame.draw.line(surface, color, (cx + math.cos(angle) * 9, cy + math.sin(angle) * 9), (cx + math.cos(angle) * 12, cy + math.sin(angle) * 12), 2)
        elif kind == "sound":
            pygame.draw.polygon(surface, color, [(cx - 8, cy - 4), (cx - 3, cy - 4), (cx + 3, cy - 10), (cx + 3, cy + 10), (cx - 3, cy + 4), (cx - 8, cy + 4)])
            pygame.draw.arc(surface, color, (cx, cy - 6, 10, 12), -1.2, 1.2, 2)
        else:
            pygame.draw.circle(surface, color, center, 5)
