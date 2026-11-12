"""Logical canvas metrics shared by every screen and app.

The display owns the window; this module owns *layout*. Screens never read the
window size directly and never hardcode a device resolution. They ask the
current :data:`layout` for the logical canvas, safe insets and a few common
grid helpers. When the device rotates, the display swaps the logical canvas
(portrait 540x1200 <-> landscape 1200x540) and calls :func:`configure`; every
screen sees the new metrics on its next draw.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from core.display import LANDSCAPE_SIZE, PORTRAIT_SIZE

STATUS_BAR_HEIGHT = 54
SAFE_MARGIN = 24


@dataclass(frozen=True, slots=True)
class LayoutSnapshot:
    """Immutable copy for code that wants to cache layout-sensitive surfaces."""

    size: tuple[int, int]
    orientation: str
    margin: int
    top: int
    bottom: int
    content: pygame.Rect


class Layout:
    """Mutable global metrics, rebuilt whenever the logical canvas changes."""

    def __init__(self, size: tuple[int, int] = PORTRAIT_SIZE) -> None:
        self.width = int(size[0])
        self.height = int(size[1])
        self.orientation = "portrait"
        self.margin = SAFE_MARGIN
        self.top = STATUS_BAR_HEIGHT + 6
        self.bottom = SAFE_MARGIN
        self.content = pygame.Rect(0, 0, 1, 1)
        self.configure(size)

    # -- lifecycle -----------------------------------------------------------
    def configure(self, size: tuple[int, int]) -> None:
        width = max(1, int(size[0]))
        height = max(1, int(size[1]))
        self.width = width
        self.height = height
        self.orientation = "landscape" if width > height else "portrait"
        side = min(width, height)
        self.margin = max(16, min(30, int(round(side * 0.045))))
        self.top = STATUS_BAR_HEIGHT + (8 if self.is_landscape else 6)
        self.bottom = max(14, min(28, int(round(side * 0.045))))
        self.content = pygame.Rect(
            self.margin,
            self.top,
            max(1, width - self.margin * 2),
            max(1, height - self.top - self.bottom),
        )

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    @property
    def is_landscape(self) -> bool:
        return self.orientation == "landscape"

    @property
    def is_portrait(self) -> bool:
        return self.orientation == "portrait"

    @property
    def compact(self) -> bool:
        """True when vertical space is tight (landscape phones)."""
        return self.height < 760

    @property
    def status_bar_height(self) -> int:
        return STATUS_BAR_HEIGHT

    @property
    def center(self) -> tuple[int, int]:
        return (self.width // 2, self.height // 2)

    @property
    def center_x(self) -> int:
        return self.width // 2

    @property
    def content_height(self) -> int:
        return self.content.h

    @property
    def columns(self) -> int:
        """Default settings/control column count for the current aspect."""
        if self.width >= 1000:
            return 3
        if self.width >= 720:
            return 2
        return 1

    # -- factory helpers -----------------------------------------------------
    def snapshot(self) -> LayoutSnapshot:
        return LayoutSnapshot(
            size=self.size,
            orientation=self.orientation,
            margin=self.margin,
            top=self.top,
            bottom=self.bottom,
            content=pygame.Rect(self.content),
        )

    def grid_rects(
        self,
        count: int,
        *,
        preferred_height: int = 72,
        columns: int | None = None,
        gap: int = 8,
        body: pygame.Rect | None = None,
    ) -> list[pygame.Rect]:
        """Row-major rectangles that fit ``count`` items into the body.

        Heights shrink to fit; they never grow above ``preferred_height`` so a
        portrait page does not stretch a single toggle over half the screen.
        """
        if count <= 0:
            return []
        area = pygame.Rect(body if body is not None else self.content)
        cols = max(1, int(columns if columns is not None else self.columns))
        cols = min(cols, count)
        rows = (count + cols - 1) // cols
        available = area.h - gap * (rows - 1)
        cell_h = max(28, min(int(preferred_height), available // max(1, rows)))
        cell_w = max(1, (area.w - gap * (cols - 1)) // cols)
        rects: list[pygame.Rect] = []
        for index in range(count):
            row, column = divmod(index, cols)
            rects.append(
                pygame.Rect(
                    area.x + column * (cell_w + gap),
                    area.y + row * (cell_h + gap),
                    cell_w,
                    cell_h,
                )
            )
        return rects

    def split(self, rect: pygame.Rect, count: int, gap: int = 8) -> list[pygame.Rect]:
        """Split a rectangle into ``count`` equal columns."""
        count = max(1, int(count))
        width = max(1, (rect.w - gap * (count - 1)) // count)
        return [
            pygame.Rect(rect.x + index * (width + gap), rect.y, width, rect.h)
            for index in range(count)
        ]


layout = Layout(PORTRAIT_SIZE)


def configure(size: tuple[int, int]) -> Layout:
    layout.configure(size)
    return layout


def safe_bottom(surface_height: int) -> int:
    """Convenience for the home indicator."""
    return max(0, int(surface_height) - 22)


__all__ = [
    "Layout",
    "LayoutSnapshot",
    "STATUS_BAR_HEIGHT",
    "SAFE_MARGIN",
    "configure",
    "layout",
    "safe_bottom",
]
