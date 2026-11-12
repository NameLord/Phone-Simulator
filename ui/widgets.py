"""Reusable controls: Slider, Toggle, Button, ListRow, SwatchPicker, TextInput.

Two rules hold for every widget:

* It draws itself with a ``Theme`` (never a colour literal) and reports whether
  it consumed an event.
* It is operable without a mouse. :class:`Form` owns focus, routes Tab / arrows
  / Enter, and forwards printable text to the focused :class:`TextInput`.

The settings screen that uses these widgets flips ``InputManager.ui_capture_keys``
so arrow keys reach the form instead of driving the virtual mouse pointer.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

import pygame

from core.input import EventKind, PointerEvent
from core.scene import clamp, ease_out_cubic, lerp
from ui.paint import (
    blit_text,
    draw_chevron,
    draw_rounded,
    fit_text,
    with_alpha,
    wrap_text,
)

RGB = tuple[int, int, int]
CARD_RADIUS = 18


def _card(
    surface: pygame.Surface,
    palette,
    rect: pygame.Rect,
    *,
    fill: RGB | None = None,
    stroke: RGB | None = None,
    alpha: int = 255,
    radius: int = CARD_RADIUS,
) -> None:
    draw_rounded(
        surface,
        rect,
        fill=fill if fill is not None else palette.surface,
        stroke=stroke if stroke is not None else palette.stroke_soft,
        radius=radius,
        alpha=alpha,
    )


TOUCH_EXPAND = 0


def set_touch_expand(pixels: int) -> None:
    global TOUCH_EXPAND
    TOUCH_EXPAND = max(0, int(pixels))


class Widget:
    """Base control."""

    focusable: bool = True

    def __init__(self, rect: pygame.Rect) -> None:
        self.rect = pygame.Rect(rect)
        self.enabled = True
        self.focused = False
        self.pressed = False
        self.request_blur = False

    def contains(self, pos: Sequence[float]) -> bool:
        if not self.enabled:
            return False
        if TOUCH_EXPAND:
            return self.rect.inflate(TOUCH_EXPAND * 2, TOUCH_EXPAND * 2).collidepoint(pos)
        return self.rect.collidepoint(pos)

    def update(self, dt: float) -> None:
        """Advance animations; stateless widgets ignore this."""

    def handle(self, event: PointerEvent) -> bool:
        return False

    def adjust(self, direction: int) -> bool:
        return False

    def activate(self) -> None:
        """Keyboard activation (Enter / Space)."""

    def draw(self, surface: pygame.Surface, theme) -> None:
        raise NotImplementedError

    def draw_focus(self, surface: pygame.Surface, theme) -> None:
        if self.focused and self.enabled:
            pygame.draw.rect(
                surface,
                theme.palette.accent,
                self.rect.inflate(6, 6),
                width=2,
                border_radius=CARD_RADIUS + 4,
            )


class Button(Widget):
    """Primary / ghost / danger button."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        on_press: Callable[[], None] | None = None,
        *,
        tone: str = "primary",
        subtitle: str = "",
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.subtitle = subtitle
        self.on_press = on_press
        self.tone = tone
        self._glow = 0.0

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self.pressed = True
                return True
            return False
        if event.kind is EventKind.POINTER_UP:
            was_pressed = self.pressed
            self.pressed = False
            return was_pressed
        if event.kind is EventKind.TAP:
            if self.contains(event.pos):
                self.activate()
                return True
        return False

    def activate(self) -> None:
        if not self.enabled:
            return
        self._glow = 1.0
        if self.on_press is not None:
            self.on_press()

    def update(self, dt: float) -> None:
        self._glow = max(0.0, self._glow - max(0.0, dt) * 4.0)

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        if self.tone == "primary":
            fill = pal.accent
            text_color = pal.on_accent
            stroke = None
        elif self.tone == "danger":
            fill = pal.danger
            text_color = pal.on_accent
            stroke = None
        elif self.tone == "ghost":
            fill = pal.surface
            text_color = pal.text
            stroke = pal.stroke
        else:
            fill = pal.surface_raised
            text_color = pal.text
            stroke = pal.stroke

        if not self.enabled:
            fill = pal.surface_alt
            text_color = pal.text_muted

        _card(surface, pal, self.rect, fill=fill, stroke=stroke, radius=20)
        if self.pressed:
            overlay = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            overlay.fill(with_alpha(pal.scrim, 34))
            surface.blit(overlay, self.rect.topleft)

        center_y = self.rect.centery - (7 if self.subtitle else 0)
        blit_text(
            surface,
            theme.font(18, True),
            self.label,
            text_color,
            center=(self.rect.centerx, center_y),
        )
        if self.subtitle:
            blit_text(
                surface,
                theme.font(12),
                self.subtitle,
                text_color,
                center=(self.rect.centerx, self.rect.centery + 18),
                alpha=200,
            )
        self.draw_focus(surface, theme)


class Toggle(Widget):
    """Row with a label and an animated iOS-style switch."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        value: bool,
        on_change: Callable[[bool], None] | None = None,
        *,
        subtitle: str = "",
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.subtitle = subtitle
        self.value = bool(value)
        self.on_change = on_change
        self._anim = 1.0 if self.value else 0.0

    def set_value(self, value: bool, *, notify: bool = False) -> None:
        value = bool(value)
        changed = value != self.value
        self.value = value
        if notify and changed and self.on_change is not None:
            self.on_change(value)

    def _flip(self) -> None:
        if not self.enabled:
            return
        self.value = not self.value
        if self.on_change is not None:
            self.on_change(self.value)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self.pressed = True
                return True
            return False
        if event.kind is EventKind.POINTER_UP:
            was = self.pressed
            self.pressed = False
            return was
        if event.kind is EventKind.TAP:
            if self.contains(event.pos):
                self._flip()
                return True
        return False

    def activate(self) -> None:
        self._flip()

    def adjust(self, direction: int) -> bool:
        desired = direction > 0
        if desired != self.value:
            self._flip()
        return True

    def update(self, dt: float) -> None:
        target = 1.0 if self.value else 0.0
        self._anim += (target - self._anim) * min(1.0, max(0.0, dt) * 14.0)

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)

        text_width = self.rect.w - (110 if self.rect.w < 300 else 120)
        label_size = 14 if self.rect.w < 300 else 17
        blit_text(
            surface,
            theme.font(label_size, True),
            fit_text(theme.font(label_size, True), self.label, text_width),
            pal.text,
            midleft=(self.rect.x + 20, self.rect.centery - (9 if self.subtitle else 0)),
        )
        if self.subtitle:
            blit_text(
                surface,
                theme.font(10),
                fit_text(theme.font(10), self.subtitle, text_width),
                pal.text_muted,
                midleft=(self.rect.x + 20, self.rect.centery + 14),
            )

        track = pygame.Rect(0, 0, 58, 32)
        track.midright = (self.rect.right - 20, self.rect.centery)
        off = pal.switch_off
        on = pal.accent
        track_color = (
            int(lerp(off[0], on[0], self._anim)),
            int(lerp(off[1], on[1], self._anim)),
            int(lerp(off[2], on[2], self._anim)),
        )
        pygame.draw.rect(surface, track_color, track, border_radius=16)
        knob_x = int(lerp(track.x + 16, track.right - 16, clamp(self._anim, 0.0, 1.0)))
        pygame.draw.circle(surface, (252, 253, 255), (knob_x, track.centery), 13)
        pygame.draw.circle(surface, with_alpha(pal.shadow, 40), (knob_x, track.centery + 1), 13, 1)

        self.draw_focus(surface, theme)


class Slider(Widget):
    """Card row with a draggable track and keyboard stepping."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        value: float,
        minimum: float,
        maximum: float,
        on_change: Callable[[float], None] | None = None,
        *,
        step: float = 0.01,
        formatter: Callable[[float], str] | None = None,
        subtitle: str = "",
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.subtitle = subtitle
        self.minimum = float(minimum)
        self.maximum = float(maximum) if maximum > minimum else self.minimum + 1e-6
        self.step = max(1e-6, float(step))
        self.on_change = on_change
        self.value = clamp(float(value), self.minimum, self.maximum)
        self.formatter = formatter or (lambda current: f"{current:.0%}")
        self._dragging = False

    # -- value ---------------------------------------------------------------
    def set_value(self, value: float, *, notify: bool = False) -> None:
        clamped = clamp(round(float(value), 4), self.minimum, self.maximum)
        changed = abs(clamped - self.value) > 1e-9
        self.value = clamped
        if notify and changed and self.on_change is not None:
            self.on_change(self.value)

    def fraction(self) -> float:
        span = self.maximum - self.minimum
        return 0.0 if span <= 0.0 else (self.value - self.minimum) / span

    def adjust(self, direction: int) -> bool:
        self.set_value(self.value + direction * self.step, notify=True)
        return True

    def _track_rect(self) -> pygame.Rect:
        return pygame.Rect(self.rect.x + 20, self.rect.bottom - 18, self.rect.w - 40, 6)

    def _set_from_x(self, x: float) -> None:
        track = self._track_rect()
        fraction = clamp((float(x) - track.x) / max(1.0, float(track.w)), 0.0, 1.0)
        raw = self.minimum + fraction * (self.maximum - self.minimum)
        stepped = round(raw / self.step) * self.step
        self.set_value(stepped, notify=True)

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self._dragging = True
                self.pressed = True
                self._set_from_x(event.pos[0])
                return True
            return False
        if event.kind is EventKind.DRAG:
            if self._dragging:
                self._set_from_x(event.pos[0])
                return True
            return False
        if event.kind is EventKind.POINTER_UP or event.kind is EventKind.DRAG_END:
            was = self._dragging or self.pressed
            self._dragging = False
            self.pressed = False
            return was
        if event.kind is EventKind.TAP:
            if self.contains(event.pos):
                self._set_from_x(event.pos[0])
                return True
        return False

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)

        blit_text(
            surface,
            theme.font(16, True),
            self.label,
            pal.text,
            topleft=(self.rect.x + 20, self.rect.y + 14),
        )
        blit_text(
            surface,
            theme.mono(14, True),
            self.formatter(self.value),
            pal.accent_ink,
            midright=(self.rect.right - 20, self.rect.y + 24),
        )
        if self.subtitle and self.rect.h >= 76:
            blit_text(
                surface,
                theme.font(11),
                self.subtitle,
                pal.text_muted,
                topleft=(self.rect.x + 20, self.rect.y + 36),
            )

        track = self._track_rect()
        pygame.draw.rect(surface, pal.track, track, border_radius=3)
        filled = pygame.Rect(track.x, track.y, max(2, int(track.w * self.fraction())), track.h)
        pygame.draw.rect(surface, pal.accent, filled, border_radius=3)

        knob_x = track.x + int(track.w * self.fraction())
        pygame.draw.circle(surface, pal.surface_raised, (knob_x, track.centery), 11)
        pygame.draw.circle(surface, pal.accent, (knob_x, track.centery), 11, 3)
        pygame.draw.circle(surface, pal.accent, (knob_x, track.centery), 3)

        self.draw_focus(surface, theme)


class ListRow(Widget):
    """Navigation row: title, optional subtitle, value and chevron."""

    def __init__(
        self,
        rect: pygame.Rect,
        title: str,
        on_press: Callable[[], None] | None = None,
        *,
        subtitle: str = "",
        value: str = "",
        chevron: bool = True,
        tone: str = "default",
    ) -> None:
        super().__init__(rect)
        self.title = title
        self.subtitle = subtitle
        self.value = value
        self.chevron = chevron
        self.on_press = on_press
        self.tone = tone

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self.pressed = True
                return True
            return False
        if event.kind is EventKind.POINTER_UP:
            was = self.pressed
            self.pressed = False
            return was
        if event.kind is EventKind.TAP:
            if self.contains(event.pos):
                self.activate()
                return True
        return False

    def activate(self) -> None:
        if self.enabled and self.on_press is not None:
            self.on_press()

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)

        title_color = pal.danger if self.tone == "danger" else pal.text
        value_width = 0
        if self.value:
            value_font = theme.font(13)
            value_width = min(self.rect.w // 2, value_font.size(self.value)[0] + 16)
            blit_text(
                surface,
                value_font,
                fit_text(value_font, self.value, value_width),
                pal.text_muted,
                midright=(self.rect.right - (34 if self.chevron else 22), self.rect.centery),
            )

        text_width = self.rect.w - 40 - value_width - (28 if self.chevron else 0)
        title_font = theme.font(17, True)
        if self.subtitle:
            blit_text(
                surface,
                title_font,
                fit_text(title_font, self.title, text_width),
                title_color,
                midleft=(self.rect.x + 20, self.rect.centery - 11),
            )
            blit_text(
                surface,
                theme.font(12),
                fit_text(theme.font(12), self.subtitle, text_width),
                pal.text_muted,
                midleft=(self.rect.x + 20, self.rect.centery + 12),
            )
        else:
            blit_text(
                surface,
                title_font,
                fit_text(title_font, self.title, text_width),
                title_color,
                midleft=(self.rect.x + 20, self.rect.centery),
            )

        if self.chevron:
            draw_chevron(
                surface,
                (self.rect.right - 20, self.rect.centery),
                pal.text_muted,
                size=6,
            )
        self.draw_focus(surface, theme)


class ChoicePicker(Widget):
    """Enum row: tap/Enter cycles forward, arrows cycle both directions."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        options: Iterable[tuple[str, str]],
        selected: str,
        on_change: Callable[[str], None] | None = None,
        *,
        subtitle: str = "",
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.options = tuple(options)
        self.selected = selected
        self.on_change = on_change
        self.subtitle = subtitle

    def _index(self) -> int:
        for index, (value, _label) in enumerate(self.options):
            if value == self.selected:
                return index
        return 0

    def _label(self) -> str:
        index = self._index()
        return self.options[index][1] if self.options else self.selected

    def select(self, value: str, *, notify: bool = False) -> None:
        if not any(option == value for option, _ in self.options):
            return
        changed = value != self.selected
        self.selected = value
        if notify and changed and self.on_change is not None:
            self.on_change(value)

    def _cycle(self, direction: int) -> None:
        if not self.options:
            return
        index = (self._index() + direction) % len(self.options)
        self.select(self.options[index][0], notify=True)

    def adjust(self, direction: int) -> bool:
        self._cycle(direction)
        return True

    def activate(self) -> None:
        self._cycle(1)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self.pressed = True
                return True
            return False
        if event.kind is EventKind.POINTER_UP:
            was = self.pressed
            self.pressed = False
            return was
        if event.kind is EventKind.TAP and self.contains(event.pos):
            self._cycle(1)
            return True
        return False

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)
        title_font = theme.font(13 if self.rect.w < 320 else (14 if self.rect.h < 76 else 16), True)
        blit_text(
            surface,
            title_font,
            fit_text(title_font, self.label, self.rect.w - 145),
            pal.text,
            midleft=(self.rect.x + 20, self.rect.centery - (8 if self.subtitle else 0)),
        )
        if self.subtitle and self.rect.h >= 72:
            blit_text(
                surface,
                theme.font(10),
                fit_text(theme.font(10), self.subtitle, self.rect.w - 145),
                pal.text_muted,
                midleft=(self.rect.x + 20, self.rect.centery + 11),
            )
        value = self._label()
        value_font = theme.font(12)
        value_width = min(124, max(58, self.rect.w // 3))
        blit_text(
            surface,
            value_font,
            fit_text(value_font, value, value_width),
            pal.accent_ink,
            midright=(self.rect.right - 36, self.rect.centery),
        )
        draw_chevron(surface, (self.rect.right - 20, self.rect.centery), pal.text_muted, size=6)
        self.draw_focus(surface, theme)


class SwatchPicker(Widget):
    """Row of accent swatches; click a swatch or cycle with arrow keys."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        options: Iterable[tuple[str, RGB]],
        selected: str,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.options = tuple(options)
        self.selected = selected
        self.on_change = on_change

    def _selected_index(self) -> int:
        for index, (name, _color) in enumerate(self.options):
            if name == self.selected:
                return index
        return 0

    def select(self, name: str, *, notify: bool = False) -> None:
        if not any(name == option for option, _ in self.options):
            return
        changed = name != self.selected
        self.selected = name
        if notify and changed and self.on_change is not None:
            self.on_change(name)

    def _cycle(self, direction: int) -> None:
        if not self.options:
            return
        index = (self._selected_index() + direction) % len(self.options)
        self.select(self.options[index][0], notify=True)

    def adjust(self, direction: int) -> bool:
        self._cycle(direction)
        return True

    def _swatch_centers(self) -> list[tuple[int, int]]:
        count = max(1, len(self.options))
        spacing = min(70, (self.rect.w - 40) // count)
        total = spacing * count
        start = self.rect.centerx - total // 2 + spacing // 2
        y = self.rect.bottom - max(16, min(28, self.rect.h // 3))
        return [(start + index * spacing, y) for index in range(count)]

    def _radius(self) -> int:
        return max(7, min(16, (self.rect.h - 40) // 2))

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.POINTER_DOWN:
            if self.contains(event.pos):
                self.pressed = True
                return True
            return False
        if event.kind is EventKind.POINTER_UP:
            was = self.pressed
            self.pressed = False
            return was
        if event.kind is EventKind.TAP:
            if not self.contains(event.pos):
                return False
            radius = self._radius() + 10
            centers = self._swatch_centers()
            for index, (name, _color) in enumerate(self.options):
                if index >= len(centers):
                    break
                cx, cy = centers[index]
                if (event.pos[0] - cx) ** 2 + (event.pos[1] - cy) ** 2 <= radius ** 2:
                    self.select(name, notify=True)
                    return True
            return True
        return False

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)

        blit_text(
            surface,
            theme.font(14 if self.rect.h < 84 else 16, True),
            self.label,
            pal.text,
            topleft=(self.rect.x + 20, self.rect.y + 10),
        )
        blit_text(
            surface,
            theme.font(11),
            self.selected.title(),
            pal.text_muted,
            midright=(self.rect.right - 20, self.rect.y + 20),
        )

        radius = self._radius()
        for index, ((name, color), (cx, cy)) in enumerate(zip(self.options, self._swatch_centers())):
            selected = name == self.selected
            if selected:
                pygame.draw.circle(surface, with_alpha(color, 70), (cx, cy), radius + 6)
                pygame.draw.circle(surface, pal.surface, (cx, cy), radius + 3)
            pygame.draw.circle(surface, color, (cx, cy), radius)
            if selected:
                pygame.draw.circle(surface, color, (cx, cy), radius + 3, 2)
                pygame.draw.circle(surface, pal.text, (cx, cy + radius + 5), 2)

        self.draw_focus(surface, theme)


class TextInput(Widget):
    """Single-line editable text field with keyboard capture."""

    def __init__(
        self,
        rect: pygame.Rect,
        label: str,
        value: str,
        on_change: Callable[[str], None] | None = None,
        *,
        max_length: int = 24,
        placeholder: str = "",
        on_submit: Callable[[str], None] | None = None,
        allowed: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(rect)
        self.label = label
        self.value = value
        self.on_change = on_change
        self.on_submit = on_submit
        self.max_length = max(1, int(max_length))
        self.placeholder = placeholder
        self.allowed = allowed
        self._caret = 0.0

    def _field_rect(self) -> pygame.Rect:
        top = self.rect.y + max(28, self.rect.h - 42)
        height = max(24, min(40, self.rect.bottom - top - 6))
        return pygame.Rect(self.rect.x + 20, top, self.rect.w - 40, height)

    def set_value(self, value: str, *, notify: bool = False) -> None:
        text = str(value)[: self.max_length]
        changed = text != self.value
        self.value = text
        if notify and changed and self.on_change is not None:
            self.on_change(text)

    def update(self, dt: float) -> None:
        if self.focused:
            self._caret = (self._caret + max(0.0, dt)) % 1.06

    def activate(self) -> None:
        self.request_blur = True
        if self.on_submit is not None:
            self.on_submit(self.value)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.TAP:
            return self.contains(event.pos)
        if event.kind is not EventKind.KEY_DOWN or not self.focused:
            return False

        if event.key == pygame.K_BACKSPACE:
            if self.value:
                self.set_value(self.value[:-1], notify=True)
            return True
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.activate()
            return True
        if event.key == pygame.K_ESCAPE:
            self.request_blur = True
            return True

        text = event.text or ""
        for character in text:
            if not character.isprintable() or character in "\t\r\n":
                continue
            if self.allowed is not None and not self.allowed(character):
                continue
            if len(self.value) >= self.max_length:
                break
            self.value += character
            if self.on_change is not None:
                self.on_change(self.value)
        return True

    def draw(self, surface: pygame.Surface, theme) -> None:
        pal = theme.palette
        _card(surface, pal, self.rect)

        blit_text(
            surface,
            theme.font(12),
            self.label,
            pal.text_secondary,
            topleft=(self.rect.x + 20, self.rect.y + 10),
        )

        box = self._field_rect()
        pygame.draw.rect(surface, pal.surface_alt, box, border_radius=10)
        pygame.draw.rect(
            surface,
            pal.accent if self.focused else pal.stroke_soft,
            box,
            width=2 if self.focused else 1,
            border_radius=10,
        )

        font = theme.font(16)
        display_value = self.value if self.value else self.placeholder
        color = pal.text if self.value else pal.text_muted
        blit_text(
            surface,
            font,
            fit_text(font, display_value, box.w - 24),
            color,
            midleft=(box.x + 12, box.centery),
        )

        if self.focused and self._caret < 0.55:
            caret_x = box.x + 12 + font.size(self.value)[0] + 2
            caret_x = min(caret_x, box.right - 8)
            pygame.draw.line(
                surface, pal.accent, (caret_x, box.y + 10), (caret_x, box.bottom - 10), 2
            )

        self.draw_focus(surface, theme)


class Form:
    """Focus ring + keyboard routing for a list of widgets."""

    def __init__(self, widgets: Sequence[Widget]) -> None:
        self.widgets: list[Widget] = list(widgets)
        self._index = -1
        self.focus_first()

    # -- focus ---------------------------------------------------------------
    @property
    def focused_widget(self) -> Widget | None:
        if 0 <= self._index < len(self.widgets):
            return self.widgets[self._index]
        return None

    @property
    def focus_index(self) -> int:
        return self._index

    def _focusable_indices(self) -> list[int]:
        return [
            index
            for index, widget in enumerate(self.widgets)
            if widget.focusable and widget.enabled
        ]

    def focus(self, widget: Widget) -> None:
        for index, candidate in enumerate(self.widgets):
            candidate.focused = candidate is widget
        self._index = self.widgets.index(widget) if widget in self.widgets else -1

    def clear_focus(self) -> None:
        for widget in self.widgets:
            widget.focused = False
        self._index = -1

    def focus_first(self) -> None:
        indices = self._focusable_indices()
        if not indices:
            self.clear_focus()
            return
        self.focus(self.widgets[indices[0]])

    def focus_last(self) -> None:
        indices = self._focusable_indices()
        if not indices:
            self.clear_focus()
            return
        self.focus(self.widgets[indices[-1]])

    def move_focus(self, delta: int) -> None:
        indices = self._focusable_indices()
        if not indices:
            self.clear_focus()
            return
        if self._index in indices:
            position = indices.index(self._index)
        else:
            position = -1 if delta > 0 else 0
        self.focus(self.widgets[indices[(position + delta) % len(indices)]])

    # -- events --------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_TAB:
                backwards = bool(event.mods & pygame.KMOD_SHIFT)
                self.move_focus(-1 if backwards else 1)
                return True
            if event.key == pygame.K_PAGEDOWN:
                self.focus_last()
                return True
            if event.key == pygame.K_PAGEUP:
                self.focus_first()
                return True

            focused = self.focused_widget
            if focused is not None and focused.handle(event):
                if focused.request_blur:
                    focused.request_blur = False
                    self.clear_focus()
                return True

            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                if focused is not None:
                    focused.activate()
                    if focused.request_blur:
                        focused.request_blur = False
                        self.clear_focus()
                    return True
                return False

            if event.key in (pygame.K_RIGHT, pygame.K_d, pygame.K_PLUS, pygame.K_EQUALS):
                if focused is not None and focused.adjust(1):
                    return True
            if event.key in (pygame.K_LEFT, pygame.K_a, pygame.K_MINUS):
                if focused is not None and focused.adjust(-1):
                    return True
            if event.key in (pygame.K_DOWN, pygame.K_s):
                self.move_focus(1)
                return True
            if event.key in (pygame.K_UP, pygame.K_w):
                self.move_focus(-1)
                return True
            return False

        if event.kind is EventKind.POINTER_DOWN:
            for widget in self.widgets:
                widget.pressed = False
            for widget in self.widgets:
                if widget.contains(event.pos):
                    if widget.focusable:
                        self.focus(widget)
                    return widget.handle(event)
            return False

        if event.kind is EventKind.TAP:
            for widget in self.widgets:
                if widget.contains(event.pos):
                    if widget.focusable:
                        self.focus(widget)
                    return widget.handle(event)
            return False

        if event.kind in (
            EventKind.POINTER_UP,
            EventKind.DRAG,
            EventKind.DRAG_START,
            EventKind.DRAG_END,
        ):
            consumed = False
            for widget in self.widgets:
                if widget.handle(event):
                    consumed = True
            return consumed

        return False

    # -- frame ---------------------------------------------------------------
    def update(self, dt: float) -> None:
        for widget in self.widgets:
            widget.update(dt)

    def draw(self, surface: pygame.Surface, theme) -> None:
        for widget in self.widgets:
            widget.draw(surface, theme)


class ConfirmDialog:
    """Modal confirmation card. Swallows every event while open."""

    CARD_WIDTH = 452
    CARD_HEIGHT = 316

    def __init__(
        self,
        bounds: pygame.Rect,
        title: str,
        message: str,
        *,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
        danger: bool = True,
        on_confirm: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
    ) -> None:
        self.bounds = pygame.Rect(bounds)
        self.title = title
        self.message = message
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label
        self.danger = danger
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.is_open = False
        self._t = 0.0
        self._focus = 0  # 0 = cancel, 1 = confirm

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        self.is_open = True
        self._t = 0.0
        self._focus = 0

    def close(self) -> None:
        self.is_open = False
        self._t = 0.0

    def update(self, dt: float) -> None:
        if self.is_open:
            self._t = min(1.0, self._t + max(0.0, dt) / 0.22)

    def _card_rect(self) -> pygame.Rect:
        scale = 0.94 + 0.06 * ease_out_cubic(self._t)
        width = int(self.CARD_WIDTH * scale)
        height = int(self.CARD_HEIGHT * scale)
        rect = pygame.Rect(0, 0, width, height)
        rect.center = self.bounds.center
        return rect

    def _button_rects(self) -> tuple[pygame.Rect, pygame.Rect]:
        card = self._card_rect()
        width = (card.w - 3 * 20) // 2
        y = card.bottom - 20 - 58
        cancel = pygame.Rect(card.x + 20, y, width, 58)
        confirm = pygame.Rect(cancel.right + 20, y, width, 58)
        return (cancel, confirm)

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False

        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._cancel()
                return True
            if event.key in (pygame.K_TAB, pygame.K_LEFT, pygame.K_RIGHT):
                self._focus = 1 - self._focus
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                if self._focus == 1:
                    self._confirm()
                else:
                    self._cancel()
                return True
            return True

        if event.kind is EventKind.TAP:
            cancel_rect, confirm_rect = self._button_rects()
            if confirm_rect.collidepoint(event.pos):
                self._confirm()
            elif cancel_rect.collidepoint(event.pos):
                self._cancel()
            return True

        if event.kind is EventKind.POINTER_DOWN:
            cancel_rect, confirm_rect = self._button_rects()
            if confirm_rect.collidepoint(event.pos):
                self._focus = 1
            elif cancel_rect.collidepoint(event.pos):
                self._focus = 0
            return True

        return True

    def _confirm(self) -> None:
        callback = self.on_confirm
        self.close()
        if callback is not None:
            callback()

    def _cancel(self) -> None:
        callback = self.on_cancel
        self.close()
        if callback is not None:
            callback()

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface, theme) -> None:
        if not self.is_open:
            return
        pal = theme.palette
        eased = ease_out_cubic(self._t)

        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill(with_alpha(pal.scrim, int(178 * eased)))
        surface.blit(scrim, (0, 0))

        card = self._card_rect()
        shadow = card.move(0, 14)
        draw_rounded(surface, shadow, fill=pal.shadow, radius=26, alpha=int(90 * eased))
        draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke, radius=26, alpha=int(255 * eased))

        inner_left = card.x + 24
        blit_text(
            surface,
            theme.font(21, True),
            self.title,
            pal.text,
            topleft=(inner_left, card.y + 24),
        )

        body_font = theme.font(14)
        lines = wrap_text(body_font, self.message, card.w - 48)[:4]
        y = card.y + 66
        for line in lines:
            blit_text(surface, body_font, line, pal.text_secondary, topleft=(inner_left, y))
            y += 21

        cancel_rect, confirm_rect = self._button_rects()
        for index, (rect, label, tone) in enumerate(
            (
                (cancel_rect, self.cancel_label, "ghost"),
                (confirm_rect, self.confirm_label, "danger" if self.danger else "primary"),
            )
        ):
            if tone == "ghost":
                fill = pal.surface_alt
                text_color = pal.text
            elif tone == "danger":
                fill = pal.danger
                text_color = pal.on_accent
            else:
                fill = pal.accent
                text_color = pal.on_accent
            draw_rounded(surface, rect, fill=fill, stroke=pal.stroke_soft, radius=16)
            blit_text(
                surface,
                theme.font(16, True),
                label,
                text_color,
                center=rect.center,
            )
            if self._focus == index:
                pygame.draw.rect(
                    surface, pal.accent, rect.inflate(6, 6), width=2, border_radius=19
                )


class ContextMenu:
    """Small modal menu used by the home screen's long-press action."""

    WIDTH = 296
    ROW_HEIGHT = 50

    def __init__(
        self,
        bounds: pygame.Rect,
        title: str,
        options: Sequence[tuple[str, Callable[[], None] | None]],
    ) -> None:
        self.bounds = pygame.Rect(bounds)
        self.title = title
        self.options = list(options)
        self.is_open = False
        self.rect = pygame.Rect(0, 0, self.WIDTH, 60 + len(self.options) * self.ROW_HEIGHT)
        self._t = 0.0
        self._focus = 0

    # -- lifecycle -----------------------------------------------------------
    def open(self, pos: Sequence[float]) -> None:
        self.is_open = True
        self._t = 0.0
        self._focus = 0
        x = int(clamp(float(pos[0]) - self.WIDTH / 2, 12, self.bounds.w - self.WIDTH - 12))
        y = int(
            clamp(
                float(pos[1]) + 18,
                132,
                self.bounds.h - self.rect.h - 24,
            )
        )
        self.rect.topleft = (x, y)

    def close(self) -> None:
        self.is_open = False
        self._t = 0.0

    def update(self, dt: float) -> None:
        if self.is_open:
            self._t = min(1.0, self._t + max(0.0, dt) / 0.16)

    def _option_rect(self, index: int) -> pygame.Rect:
        return pygame.Rect(
            self.rect.x,
            self.rect.y + 44 + index * self.ROW_HEIGHT,
            self.rect.w,
            self.ROW_HEIGHT,
        )

    # -- input ---------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if not self.is_open:
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self.close()
            elif event.key in (pygame.K_DOWN, pygame.K_TAB):
                self._focus = (self._focus + 1) % max(1, len(self.options))
            elif event.key == pygame.K_UP:
                self._focus = (self._focus - 1) % max(1, len(self.options))
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self._activate(self._focus)
            return True
        if event.kind is EventKind.TAP:
            for index in range(len(self.options)):
                if self._option_rect(index).collidepoint(event.pos):
                    self._activate(index)
                    return True
            self.close()
            return True
        return True

    def _activate(self, index: int) -> None:
        if not (0 <= index < len(self.options)):
            self.close()
            return
        _label, callback = self.options[index]
        self.close()
        if callback is not None:
            callback()

    # -- drawing -------------------------------------------------------------
    def draw(self, surface: pygame.Surface, theme) -> None:
        if not self.is_open:
            return
        pal = theme.palette
        eased = ease_out_cubic(self._t)

        scrim = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        scrim.fill(with_alpha(pal.scrim, int(80 * eased)))
        surface.blit(scrim, (0, 0))

        rect = self.rect.copy()
        if eased < 1.0:
            rect = rect.inflate(-int(12 * (1.0 - eased)), -int(8 * (1.0 - eased)))
        draw_rounded(surface, rect.move(0, 8), fill=pal.shadow, radius=20, alpha=int(70 * eased))
        draw_rounded(
            surface,
            rect,
            fill=pal.surface_raised,
            stroke=pal.stroke,
            radius=20,
            alpha=int(255 * eased),
        )
        blit_text(
            surface,
            theme.font(11),
            self.title.upper(),
            pal.text_muted,
            topleft=(rect.x + 18, rect.y + 14),
            alpha=int(255 * eased),
            tracking=1.6,
        )

        for index, (label, callback) in enumerate(self.options):
            row = self._option_rect(index)
            row.height = self.ROW_HEIGHT
            if index == self._focus:
                pygame.draw.rect(
                    surface,
                    pal.surface,
                    row.inflate(-12, -6),
                    border_radius=10,
                )
            enabled = callback is not None
            blit_text(
                surface,
                theme.font(14, enabled),
                label,
                pal.text if enabled else pal.text_muted,
                midleft=(rect.x + 18, row.centery),
                alpha=int(255 * eased),
            )
