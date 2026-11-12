"""Short-lived stacked toast messages with optional Undo actions.

Toasts never consume general input: :meth:`handle` returns True only when the
action button itself is tapped.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import pygame

from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

MAX_TOASTS = 3
DEFAULT_DURATION = 2.6


@dataclass(slots=True)
class Toast:
    message: str
    action_label: str | None = None
    action: Callable[[], None] | None = None
    duration: float = DEFAULT_DURATION
    born: float = field(default_factory=time.monotonic)
    uid: int = 0

    def age(self) -> float:
        return time.monotonic() - self.born

    def remaining(self) -> float:
        return max(0.0, self.duration - self.age())


class ToastService:
    def __init__(self) -> None:
        self._items: list[Toast] = []
        self._next = 1
        self._rects: list[tuple[Toast, pygame.Rect, pygame.Rect | None]] = []

    @property
    def items(self) -> tuple[Toast, ...]:
        return tuple(self._items)

    def show(
        self,
        message: str,
        *,
        action_label: str | None = None,
        action: Callable[[], None] | None = None,
        duration: float = DEFAULT_DURATION,
    ) -> Toast:
        toast = Toast(str(message), action_label, action, max(0.8, float(duration)), time.monotonic(), self._next)
        self._next += 1
        self._items.append(toast)
        del self._items[:-MAX_TOASTS]
        return toast

    def undo(self, message: str, callback: Callable[[], None], *, duration: float = 4.0) -> Toast:
        return self.show(message, action_label="Undo", action=callback, duration=duration)

    def clear(self) -> None:
        self._items.clear()
        self._rects.clear()

    def update(self, dt: float) -> None:
        _ = dt
        now = time.monotonic()
        self._items = [toast for toast in self._items if now - toast.born < toast.duration]

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is not EventKind.TAP:
            return False
        for toast, card, action_rect in reversed(self._rects):
            if action_rect is not None and action_rect.collidepoint(event.pos):
                try:
                    if toast.action is not None:
                        toast.action()
                finally:
                    self._items = [item for item in self._items if item.uid != toast.uid]
                return True
            if card.collidepoint(event.pos):
                # Tapping the toast body dismisses it but blocks nothing below.
                self._items = [item for item in self._items if item.uid != toast.uid]
                return True
        return False

    def draw(self, surface: pygame.Surface, theme) -> None:
        if not self._items:
            self._rects = []
            return
        pal = theme.palette
        width = min(surface.get_width() - 36, 470)
        height = 52
        gap = 8
        bottom = surface.get_height() - current_layout.bottom - 14
        self._rects = []
        for index, toast in enumerate(reversed(self._items)):
            y = bottom - height - index * (height + gap)
            rect = pygame.Rect((surface.get_width() - width) // 2, y, width, height)
            appear = min(1.0, toast.age() / 0.18)
            alpha = int(235 * appear)
            draw_rounded(surface, rect.move(0, 6), fill=pal.shadow, radius=18, alpha=70)
            draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=18, alpha=alpha)
            font = theme.font(13, True)
            text_width = rect.w - 40
            action_rect: pygame.Rect | None = None
            if toast.action_label:
                action_rect = pygame.Rect(rect.right - 92, rect.y + 10, 78, rect.h - 20)
                text_width = action_rect.x - rect.x - 34
                draw_rounded(surface, action_rect, fill=pal.accent_soft, stroke=pal.accent, radius=12, alpha=alpha)
                blit_text(surface, theme.font(11, True), toast.action_label, pal.accent_ink, center=action_rect.center, alpha=alpha)
            blit_text(surface, font, fit_text(font, toast.message, max(30, text_width)), pal.text, midleft=(rect.x + 18, rect.centery), alpha=alpha)
            self._rects.append((toast, rect, action_rect))
