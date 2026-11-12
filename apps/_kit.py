"""Small shared UI kit for the procedural app collection.

This module is not an app: it only contains reusable drawing primitives,
scrolling state and a minimal text field. Keeping it separate lets the new
apps share behaviour (wheel + drag + arrow scrolling, editable text) without
importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from core.input import EventKind, PointerEvent
from ui.paint import blit_text, draw_rounded, fit_text, with_alpha


def draw_button(
    surface: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    pal,
    *,
    fill=None,
    color=None,
    stroke=None,
    radius: int = 12,
    tracking: float = 0.0,
) -> pygame.Rect:
    draw_rounded(
        surface,
        rect,
        fill=fill if fill is not None else pal.surface_raised,
        stroke=stroke if stroke is not None else pal.stroke_soft,
        radius=radius,
    )
    blit_text(
        surface,
        font,
        label,
        color if color is not None else pal.text,
        center=rect.center,
        tracking=tracking,
    )
    return pygame.Rect(rect)


def draw_card(
    surface: pygame.Surface,
    rect: pygame.Rect,
    pal,
    *,
    selected: bool = False,
    fill=None,
    radius: int = 14,
) -> None:
    draw_rounded(
        surface,
        rect,
        fill=fill if fill is not None else (pal.surface_raised if selected else pal.surface),
        stroke=pal.accent if selected else pal.stroke_soft,
        width=2 if selected else 1,
        radius=radius,
    )


def draw_scrollbar(
    surface: pygame.Surface,
    area: pygame.Rect,
    offset: float,
    content_height: float,
    pal,
    *,
    horizontal: bool = False,
) -> None:
    """Thin scroll indicator. It disappears when everything fits."""
    if content_height <= (area.w if horizontal else area.h) + 1:
        return
    track = (
        pygame.Rect(area.x + 2, area.bottom - 4, area.w - 4, 2)
        if horizontal
        else pygame.Rect(area.right - 4, area.y + 2, 2, area.h - 4)
    )
    pygame.draw.rect(surface, pal.track, track, border_radius=1)
    view = area.w if horizontal else area.h
    total = max(view + 1.0, content_height)
    length = max(18, int(track.w * view / total)) if horizontal else max(18, int(track.h * view / total))
    max_offset = max(1.0, total - view)
    ratio = max(0.0, min(1.0, offset / max_offset))
    if horizontal:
        knob = pygame.Rect(track.x + int((track.w - length) * ratio), track.y, length, track.h)
    else:
        knob = pygame.Rect(track.x, track.y + int((track.h - length) * ratio), track.w, length)
    pygame.draw.rect(surface, pal.accent, knob, border_radius=1)


@dataclass(slots=True)
class ScrollState:
    """Wheel + drag + keyboard scroller for an arbitrary list area."""

    offset: float = 0.0
    content_height: float = 0.0
    view_height: float = 0.0
    step: int = 54
    _press_y: float | None = None
    _start_offset: float = 0.0
    _dragging: bool = False

    def reset(self) -> None:
        self.offset = 0.0
        self._press_y = None
        self._dragging = False

    def set_metrics(self, view_height: float, content_height: float) -> None:
        self.view_height = max(1.0, float(view_height))
        self.content_height = max(0.0, float(content_height))
        self.clamp()

    def max_offset(self) -> float:
        return max(0.0, self.content_height - self.view_height)

    def clamp(self) -> None:
        limit = self.max_offset()
        self.offset = max(0.0, min(limit, float(self.offset)))

    def wheel(self, event: PointerEvent) -> bool:
        if event.kind is not EventKind.WHEEL:
            return False
        self.offset -= event.delta[1] * self.step
        self.clamp()
        return True

    def key(self, event: PointerEvent, page: int = 6) -> bool:
        if event.kind is not EventKind.KEY_DOWN:
            return False
        if event.key in (pygame.K_UP, pygame.K_w):
            self.offset -= self.step
        elif event.key in (pygame.K_DOWN, pygame.K_s):
            self.offset += self.step
        elif event.key == pygame.K_PAGEUP:
            self.offset -= self.step * page
        elif event.key == pygame.K_PAGEDOWN:
            self.offset += self.step * page
        elif event.key == pygame.K_HOME:
            self.offset = 0.0
        elif event.key == pygame.K_END:
            self.offset = self.max_offset()
        else:
            return False
        self.clamp()
        return True

    def pointer(self, event: PointerEvent, area: pygame.Rect) -> bool:
        """Drag-to-scroll inside ``area``; taps still reach the app."""
        if event.kind is EventKind.POINTER_DOWN:
            if area.collidepoint(event.pos):
                self._press_y = float(event.pos[1])
                self._start_offset = self.offset
                self._dragging = False
            return False
        if event.kind is EventKind.DRAG and self._press_y is not None:
            dy = float(event.pos[1]) - self._press_y
            if abs(dy) > 8.0:
                self._dragging = True
            if self._dragging:
                self.offset = self._start_offset - dy
                self.clamp()
            return self._dragging
        if event.kind in (EventKind.POINTER_UP, EventKind.DRAG_END):
            was = self._dragging
            self._press_y = None
            self._dragging = False
            return was
        return False

    @property
    def dragging(self) -> bool:
        return self._dragging


class TextField:
    """Minimal single/multi-line editable text with a caret."""

    def __init__(
        self,
        value: str = "",
        *,
        max_length: int = 120,
        multiline: bool = False,
        placeholder: str = "",
    ) -> None:
        self.value = str(value)
        self.caret = len(self.value)
        self.max_length = max(1, int(max_length))
        self.multiline = bool(multiline)
        self.placeholder = placeholder

    def set(self, value: str) -> None:
        self.value = str(value)[: self.max_length]
        self.caret = len(self.value)

    def clear(self) -> None:
        self.value = ""
        self.caret = 0

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is not EventKind.KEY_DOWN:
            return False
        if event.key == pygame.K_LEFT:
            self.caret = max(0, self.caret - 1)
        elif event.key == pygame.K_RIGHT:
            self.caret = min(len(self.value), self.caret + 1)
        elif event.key == pygame.K_HOME:
            self.caret = 0
        elif event.key == pygame.K_END:
            self.caret = len(self.value)
        elif event.key in (pygame.K_UP, pygame.K_DOWN):
            if not self.multiline:
                return False
            line_start = self.value.rfind("\n", 0, self.caret) + 1
            column = self.caret - line_start
            if event.key == pygame.K_UP:
                if line_start == 0:
                    return True
                previous_start = self.value.rfind("\n", 0, line_start - 1) + 1
                previous_end = line_start - 1
                self.caret = min(previous_end, previous_start + column)
            else:
                line_end = self.value.find("\n", self.caret)
                if line_end == -1:
                    return True
                next_start = line_end + 1
                next_end = self.value.find("\n", next_start)
                if next_end == -1:
                    next_end = len(self.value)
                self.caret = min(next_end, next_start + column)
        elif event.key == pygame.K_BACKSPACE:
            if self.caret > 0:
                self.value = self.value[: self.caret - 1] + self.value[self.caret :]
                self.caret -= 1
        elif event.key == pygame.K_DELETE:
            if self.caret < len(self.value):
                self.value = self.value[: self.caret] + self.value[self.caret + 1 :]
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            if not self.multiline:
                return False
            if len(self.value) < self.max_length:
                self.value = self.value[: self.caret] + "\n" + self.value[self.caret :]
                self.caret += 1
        else:
            text = event.text or ""
            for character in text:
                if character.isprintable() and (self.multiline or character not in "\r\n\t"):
                    if len(self.value) >= self.max_length:
                        break
                    self.value = self.value[: self.caret] + character + self.value[self.caret :]
                    self.caret += 1
        return True

    def draw(
        self,
        surface: pygame.Surface,
        rect: pygame.Rect,
        font: pygame.font.Font,
        pal,
        *,
        focused: bool = True,
        blink: float = 0.0,
    ) -> None:
        draw_rounded(
            surface,
            rect,
            fill=pal.surface,
            stroke=pal.accent if focused else pal.stroke_soft,
            radius=10,
        )
        text = self.value if self.value else self.placeholder
        color = pal.text if self.value else pal.text_muted
        line = text.split("\n")[0] if not self.multiline else text.replace("\n", " ")
        blit_text(
            surface,
            font,
            fit_text(font, line, rect.w - 20),
            color,
            midleft=(rect.x + 10, rect.centery),
        )
        if focused and self.value and (blink % 1.06) < 0.55:
            prefix = self.value[: self.caret].split("\n")[-1]
            caret_x = rect.x + 10 + min(font.size(prefix)[0], rect.w - 24)
            pygame.draw.line(
                surface,
                pal.accent,
                (caret_x, rect.y + 10),
                (caret_x, rect.bottom - 10),
                2,
            )


def format_duration(seconds: float, *, hundredths: bool = False) -> str:
    seconds = max(0.0, float(seconds))
    if hundredths:
        minutes, rest = divmod(seconds, 60.0)
        return f"{int(minutes):02d}:{rest:05.2f}"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_timer(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


__all__ = [
    "ScrollState",
    "TextField",
    "draw_button",
    "draw_card",
    "draw_scrollbar",
    "format_duration",
    "format_timer",
    "with_alpha",
]
