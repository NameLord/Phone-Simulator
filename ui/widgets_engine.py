"""Home-widget layout, drawing and failure isolation."""

from __future__ import annotations

import logging
import traceback

import pygame

from widgets import WIDGETS, WidgetContext

LOG = logging.getLogger("phoneos.widgets")
FALLBACK_KIND = "clock"


class WidgetEngine:
    def __init__(self) -> None:
        self._instances: dict[str, object] = {}
        self._errors: dict[str, str] = {}

    # -- instances -----------------------------------------------------------
    def _instance(self, widget: dict):
        widget_id = str(widget.get("id", ""))
        kind = str(widget.get("type", ""))
        instance = self._instances.get(widget_id)
        if instance is None or getattr(instance, "kind", None) != kind:
            factory = WIDGETS.get(kind)
            if factory is None:
                factory = WIDGETS[FALLBACK_KIND]
            instance = factory(widget.get("options", {}))
            self._instances[widget_id] = instance
        instance.options = widget.get("options", {})
        return instance

    # -- cells ---------------------------------------------------------------
    @staticmethod
    def occupied(widgets: list[dict]) -> set[tuple[int, int]]:
        cells: set[tuple[int, int]] = set()
        for widget in widgets:
            for row in range(int(widget.get("row", 0)), int(widget.get("row", 0)) + int(widget.get("h", 1))):
                for col in range(int(widget.get("col", 0)), int(widget.get("col", 0)) + int(widget.get("w", 1))):
                    cells.add((col, row))
        return cells

    @staticmethod
    def free_cells(widgets: list[dict], cols: int, rows: int) -> list[int]:
        occupied = WidgetEngine.occupied(widgets)
        return [row * cols + col for row in range(rows) for col in range(cols) if (col, row) not in occupied]

    @staticmethod
    def entry_rank_for_slot(slot: int, widgets: list[dict], cols: int, rows: int) -> int | None:
        """Rank of a visible cell among free cells (0 = first icon)."""
        free = WidgetEngine.free_cells(widgets, cols, rows)
        try:
            return free.index(slot)
        except ValueError:
            return None

    @staticmethod
    def entry_index_for_slot(slot: int, widgets: list[dict], cols: int, rows: int) -> int | None:
        return WidgetEngine.entry_rank_for_slot(slot, widgets, cols, rows)

    @staticmethod
    def cell_rect(tiles: list[pygame.Rect], cols: int, col: int, row: int, width: int = 1, height: int = 1) -> pygame.Rect:
        if not tiles or cols <= 0:
            return pygame.Rect(0, 0, 0, 0)
        pitch_x = tiles[1].x - tiles[0].x if len(tiles) > 1 else tiles[0].w
        pitch_y = tiles[cols].y - tiles[0].y if len(tiles) > cols else tiles[0].h
        tile_w = tiles[0].w
        tile_h = tiles[0].h
        rect = pygame.Rect(
            int(tiles[0].x + col * pitch_x),
            int(tiles[0].y + row * pitch_y),
            int(width * tile_w + (width - 1) * max(0, pitch_x - tile_w)),
            int(height * tile_h + (height - 1) * max(0, pitch_y - tile_h)),
        )
        return rect

    def find_free_rect(self, widgets: list[dict], cols: int, rows: int, width: int, height: int) -> tuple[int, int] | None:
        occupied = self.occupied(widgets)
        for row in range(0, max(1, rows - height + 1)):
            for col in range(0, max(1, cols - width + 1)):
                cells = [(col + dx, row + dy) for dy in range(height) for dx in range(width)]
                if all(cell not in occupied for cell in cells):
                    return col, row
        return None

    # -- interaction ---------------------------------------------------------
    @staticmethod
    def hit(widgets: list[dict], tiles: list[pygame.Rect], cols: int, pos: tuple[float, float]) -> dict | None:
        for widget in reversed(widgets):
            rect = WidgetEngine.cell_rect(
                tiles,
                cols,
                int(widget.get("col", 0)),
                int(widget.get("row", 0)),
                int(widget.get("w", 1)),
                int(widget.get("h", 1)),
            )
            if rect.collidepoint(pos):
                return widget
        return None

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface, widgets: list[dict], tiles: list[pygame.Rect], cols: int, ctx: WidgetContext) -> None:
        for widget in widgets:
            kind = str(widget.get("type", ""))
            instance = self._instance(widget)
            rect = self.cell_rect(
                tiles,
                cols,
                int(widget.get("col", 0)),
                int(widget.get("row", 0)),
                int(widget.get("w", 1)),
                int(widget.get("h", 1)),
            )
            if rect.w <= 0 or rect.h <= 0:
                continue
            try:
                instance.draw(surface, rect, ctx)
                self._errors.pop(str(widget.get("id", "")), None)
            except Exception as exc:  # pragma: no cover - defensive isolation
                widget_id = str(widget.get("id", ""))
                self._errors[widget_id] = f"{kind}: {exc}"
                LOG.warning("widget %s failed: %s\n%s", kind, exc, traceback.format_exc())
                self._draw_error(surface, rect, ctx, kind)

    def _draw_error(self, surface: pygame.Surface, rect: pygame.Rect, ctx: WidgetContext, kind: str) -> None:
        from ui.paint import blit_text, draw_rounded

        draw_rounded(surface, rect, fill=ctx.palette.surface, stroke=ctx.palette.danger, radius=18, alpha=180)
        blit_text(surface, ctx.font(10, True), f"{kind} unavailable", ctx.palette.danger, center=rect.center)
        blit_text(surface, ctx.font(8), "Long-press to remove", ctx.palette.text_muted, center=(rect.centerx, rect.centery + 18))

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def forget(self, widget_id: str) -> None:
        self._instances.pop(widget_id, None)
        self._errors.pop(widget_id, None)
