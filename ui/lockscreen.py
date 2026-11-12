"""Lock screen: clock, battery, 4-digit PIN entry and swipe-up fallback.

The PIN lives in the settings store (``security.pin``, default ``"0000"``) so a
future security page can change it without touching this scene. Failed attempts
are persisted, which lets the cooldown survive a scene rebuild.

Swipe-up unlock is only active when ``security.pin_enabled`` is false, matching
the behaviour of a device with no passcode configured.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import pygame

from core.input import Direction, EventKind, PointerEvent
from core.scene import Scene, clamp, ease_in_cubic, pulse
from ui.layout import layout as current_layout
from ui.paint import (
    blit_text,
    draw_chevron,
    draw_home_indicator,
    draw_lock_glyph,
    draw_rounded,
    make_glow,
    vertical_gradient,
    with_alpha,
)

if TYPE_CHECKING:
    from ui.theme import Palette

PIN_LENGTH = 4
MAX_FREE_ATTEMPTS = 3
SHAKE_TIME = 0.48
KEY_RADIUS = 42
KEY_PITCH = 100
KEY_TOP = 648
UNLOCK_TIME = 0.30


class LockScene(Scene):
    """Real lock screen (replaces the phase-1 placeholder)."""

    name = "lock"

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self._bg_key: tuple[object, ...] | None = None
        self._background: pygame.Surface | None = None
        self._glow: pygame.Surface | None = None
        self._veil = pygame.Surface(self.size, pygame.SRCALPHA)

        self._entry = ""
        self._shake = 0.0
        self._error = ""
        self._cooldown = 0.0
        self._pressed_key: int | None = None
        self._unlocking = False
        self._unlock_t = 0.0
        self._navigated = False
        self._elapsed_local = 0.0

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self._veil = pygame.Surface(new, pygame.SRCALPHA)
        self._bg_key = None
        self._background = None
        self._glow = None

    # -- lifecycle ------------------------------------------------------------
    def on_enter(self) -> None:
        super().on_enter()
        self._entry = ""
        self._shake = 0.0
        self._error = ""
        self._pressed_key = None
        self._unlocking = False
        self._unlock_t = 0.0
        self._navigated = False

        attempts = self.settings.get_int("security.failed_attempts", 0)
        self._cooldown = self._cooldown_for(attempts) if attempts >= MAX_FREE_ATTEMPTS else 0.0
        if self._cooldown > 0.0:
            self._error = f"Too many attempts \u00b7 {int(math.ceil(self._cooldown))}s"

    # -- state helpers ---------------------------------------------------------
    @property
    def pin_enabled(self) -> bool:
        return self.settings.get_bool("security.pin_enabled", True)

    @staticmethod
    def _cooldown_for(attempts: int) -> float:
        if attempts < MAX_FREE_ATTEMPTS:
            return 0.0
        return float(min(30, 10 * (attempts - MAX_FREE_ATTEMPTS + 1)))

    def _ensure_background(self) -> "Palette":
        palette = self.theme.palette
        key = self.theme.signature
        if self._bg_key != key or self._background is None or self._glow is None:
            self._bg_key = key
            self._background = vertical_gradient(self.size, palette.bg_top, palette.bg_bottom)
            glow = make_glow(240, palette.accent, power=2.1)
            self._glow = pygame.transform.smoothscale(glow, (880, 880))
        return palette

    # -- update ---------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        self._elapsed_local += dt
        if self._shake > 0.0:
            self._shake = max(0.0, self._shake - dt)

        if self._cooldown > 0.0:
            self._cooldown = max(0.0, self._cooldown - dt)
            if self._cooldown <= 0.0:
                self._error = ""
            else:
                attempts = self.settings.get_int("security.failed_attempts", 0)
                self._error = f"Too many attempts \u00b7 {int(math.ceil(self._cooldown))}s"
                if attempts < MAX_FREE_ATTEMPTS and not self._entry:
                    self._error = ""

        if self._unlocking:
            self._unlock_t += dt
            if self._unlock_t >= UNLOCK_TIME and not self._navigated:
                self._navigated = True
                self.go("home", fade=0.20)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._unlocking:
            return True

        if event.kind is EventKind.KEY_DOWN:
            return self._handle_key(event)

        if not self.pin_enabled:
            if event.kind is EventKind.SWIPE and event.direction is Direction.UP:
                self._unlock()
                return True
            if event.kind is EventKind.DOUBLE_TAP:
                self._unlock()
                return True
            if event.kind is EventKind.TAP and event.pos[1] > 880:
                self._unlock()
                return True
            if event.kind is EventKind.DRAG_END and event.distance > 70.0 and event.delta[1] < 0:
                self._unlock()
                return True
            return False

        if event.kind is EventKind.POINTER_DOWN:
            index = self._key_at(event.pos)
            self._pressed_key = index
            return index is not None

        if event.kind is EventKind.POINTER_UP:
            was = self._pressed_key is not None
            self._pressed_key = None
            return was

        if event.kind is EventKind.TAP:
            index = self._key_at(event.pos)
            if index is None:
                return False
            self._press_key(index)
            return True

        return False

    def _handle_key(self, event: PointerEvent) -> bool:
        text = event.text or ""
        key = event.key

        if text.isdigit():
            self._press_digit(text)
            return True
        if key is not None and pygame.K_0 <= key <= pygame.K_9:
            self._press_digit(chr(key))
            return True
        if key is not None and pygame.K_KP0 <= key <= pygame.K_KP9:
            self._press_digit(str(key - pygame.K_KP0))
            return True
        if key == pygame.K_BACKSPACE:
            self._entry = self._entry[:-1]
            return True
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._submit()
            return True
        return False

    # -- PIN ------------------------------------------------------------------
    def _press_key(self, index: int) -> None:
        labels = self._key_labels()
        if index >= len(labels):
            return
        label = labels[index]
        if label == "back":
            if self._cooldown <= 0.0:
                self._entry = self._entry[:-1]
            return
        if label:
            self._press_digit(label)

    def _press_digit(self, digit: str) -> None:
        if self._cooldown > 0.0 or self._unlocking:
            return
        if not digit.isdigit() or len(self._entry) >= PIN_LENGTH:
            return
        self._entry += digit
        if len(self._entry) == PIN_LENGTH:
            self._submit()

    def _submit(self) -> None:
        if self._cooldown > 0.0 or self._unlocking:
            return
        if len(self._entry) < PIN_LENGTH:
            return

        expected = self.settings.get_str("security.pin", "0000")
        if self._entry == expected:
            self.settings.set("security.failed_attempts", 0)
            self._error = ""
            self._unlock()
            return

        attempts = self.settings.get_int("security.failed_attempts", 0) + 1
        self.settings.set("security.failed_attempts", attempts)
        self._entry = ""
        self._shake = SHAKE_TIME

        remaining = MAX_FREE_ATTEMPTS - attempts
        if remaining > 0:
            suffix = "attempt" if remaining == 1 else "attempts"
            self._error = f"Wrong PIN \u00b7 {remaining} {suffix} left"
        else:
            self._cooldown = self._cooldown_for(attempts)
            self._error = f"Too many attempts \u00b7 {int(math.ceil(self._cooldown))}s"

    def _unlock(self) -> None:
        if self._unlocking:
            return
        device = self.registry.get("device")
        if device is not None:
            device.unlock()
        fx = self.registry.get("fx")
        if fx is not None:
            fx.randomize_on_unlock()
        self._unlocking = True
        self._unlock_t = 0.0
        self._entry = ""

    # -- geometry -------------------------------------------------------------
    @staticmethod
    def _key_labels() -> tuple[str, ...]:
        return ("1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "back")

    def _key_centers(self) -> list[tuple[int, int]]:
        centers: list[tuple[int, int]] = []
        if current_layout.is_landscape:
            pitch = int(max(64, min(88, self.size[1] * 0.17)))
            center_x = int(self.size[0] * 0.72)
            top = int(self.size[1] * 0.33)
        else:
            pitch = int(min(KEY_PITCH, self.size[0] * 0.185))
            center_x = self.size[0] // 2
            top = int(self.size[1] * 0.54)
        for index in range(12):
            row, column = divmod(index, 3)
            cx = center_x + (column - 1) * pitch
            cy = top + row * pitch
            centers.append((cx, cy))
        return centers

    def _key_radius(self) -> int:
        return int(max(26, min(KEY_RADIUS, self.size[1] * 0.037)))

    def _focus_x(self) -> int:
        return int(self.size[0] * 0.28) if current_layout.is_landscape else self.size[0] // 2

    def _focus_y(self, fraction: float) -> int:
        return int(self.size[1] * fraction)

    def _hint_y(self) -> int:
        return self.size[1] - 18 if current_layout.is_landscape else int(self.size[1] * 0.887)

    def _key_at(self, pos: tuple[float, float]) -> int | None:
        if self._cooldown > 0.0:
            return None
        radius = self._key_radius() + 8
        for index, (cx, cy) in enumerate(self._key_centers()):
            if (pos[0] - cx) ** 2 + (pos[1] - cy) ** 2 <= radius ** 2:
                return index
        return None

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        palette = self._ensure_background()
        assert self._background is not None and self._glow is not None

        fx = self.registry.get("fx")
        if fx is not None:
            fx.draw_blurred_wallpaper(surface, radius=14)
            # A light veil keeps PIN dots and clock legible on bright wallpapers.
            self._veil.fill((0, 0, 0, 42 if self.theme.is_dark else 24))
            surface.blit(self._veil, (0, 0))
        else:
            surface.blit(self._background, (0, 0))
        glow = self._glow
        glow.set_alpha(int(52 + int(22 * pulse(self.elapsed, 3.6))))
        glow_center = (
            int(self.size[0] * 0.28) if current_layout.is_landscape else self.size[0] // 2,
            self._focus_y(0.30),
        )
        surface.blit(glow, glow.get_rect(center=glow_center))

        unlock_progress = clamp(self._unlock_t / UNLOCK_TIME, 0.0, 1.0) if self._unlocking else 0.0
        dy = int(-120 * ease_in_cubic(unlock_progress))
        alpha = int(255 * (1.0 - unlock_progress))

        self._draw_clock(surface, palette, dy, alpha)
        self._draw_battery(surface, palette, dy, alpha)

        if self.pin_enabled:
            self._draw_pin(surface, palette, dy, alpha)
        else:
            self._draw_open_prompt(surface, palette, dy, alpha)

        if not self._unlocking:
            draw_home_indicator(surface, palette.home_indicator)

    def _draw_clock(self, surface: pygame.Surface, pal: "Palette", dy: int, alpha: int) -> None:
        cx = self._focus_x()
        if current_layout.is_landscape:
            locked_y, time_y, date_y = 0.12, 0.225, 0.315
        else:
            locked_y, time_y, date_y = 0.163, 0.227, 0.287
        blit_text(
            surface,
            self.font(16),
            "LOCKED",
            pal.text_muted,
            center=(cx, self._focus_y(locked_y) + dy),
            alpha=int(alpha * 0.9),
            tracking=7,
        )
        blit_text(
            surface,
            self.font(76 if current_layout.is_landscape else 104, True),
            self.device_clock.time_string(),
            pal.text,
            center=(cx, self._focus_y(time_y) + dy),
            alpha=alpha,
        )
        blit_text(
            surface,
            self.font(17),
            self.device_clock.date_string(),
            pal.text_secondary,
            center=(cx, self._focus_y(date_y) + dy),
            alpha=alpha,
        )

    def _draw_battery(self, surface: pygame.Surface, pal: "Palette", dy: int, alpha: int) -> None:
        percent = self.device_clock.battery_percent
        rect = pygame.Rect(0, 0, 158, 34)
        battery_y = 0.385 if current_layout.is_landscape else 0.333
        rect.center = (self._focus_x(), self._focus_y(battery_y) + dy)
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=17, alpha=alpha)

        inner = rect.inflate(-6, -6)
        filled = int(inner.w * clamp(percent / 100.0, 0.0, 1.0))
        if filled > 0:
            bar = pygame.Rect(inner.x, inner.y, max(8, filled), inner.h)
            pygame.draw.rect(
                surface, with_alpha(pal.accent, int(alpha * 0.35)), bar, border_radius=13
            )

        blit_text(
            surface,
            self.mono(13, True),
            f"{percent:.0f}%",
            pal.text,
            center=(rect.centerx, rect.centery),
            alpha=alpha,
        )

    def _draw_pin(self, surface: pygame.Surface, pal: "Palette", dy: int, alpha: int) -> None:
        cx = self._focus_x()
        pin_y = self._focus_y(0.60 if current_layout.is_landscape else 0.445)
        blit_text(
            surface,
            self.font(13),
            "ENTER PIN",
            pal.text_muted,
            center=(cx, pin_y - 48 + dy),
            alpha=int(alpha * 0.9),
            tracking=5,
        )

        shake_ratio = self._shake / SHAKE_TIME if SHAKE_TIME > 0 else 0.0
        shake_x = math.sin(self._shake * 46.0) * 13.0 * shake_ratio if self._shake > 0 else 0.0

        start_x = cx - int((PIN_LENGTH - 1) * 40 / 2)
        for index in range(PIN_LENGTH):
            dot_x = start_x + index * 40 + int(shake_x)
            cy = pin_y + dy
            if index < len(self._entry):
                pygame.draw.circle(surface, with_alpha(pal.accent, alpha), (dot_x, cy), 11)
            else:
                pygame.draw.circle(surface, with_alpha(pal.stroke, alpha), (dot_x, cy), 10, 2)

        if self._error:
            blit_text(
                surface,
                self.font(13),
                self._error,
                pal.danger if self._cooldown <= 0.0 else pal.warning,
                center=(cx + int(shake_x * 0.4), pin_y + 52 + dy),
                alpha=alpha,
            )

        labels = self._key_labels()
        for index, (cx, cy) in enumerate(self._key_centers()):
            label = labels[index]
            if not label:
                continue
            pressed = self._pressed_key == index
            disabled = self._cooldown > 0.0
            fill = pal.keypad_active if pressed else pal.keypad
            if disabled:
                fill = pal.surface_alt
            radius = self._key_radius()
            pygame.draw.circle(surface, with_alpha(fill, alpha), (cx, cy + dy), radius)
            pygame.draw.circle(
                surface,
                with_alpha(pal.stroke_soft, alpha),
                (cx, cy + dy),
                radius,
                1,
            )

            glyph_color = pal.text_muted if disabled else pal.text
            if label == "back":
                draw_chevron(
                    surface,
                    (cx - 2, cy + dy),
                    with_alpha(glyph_color, alpha),
                    size=7,
                    thickness=2,
                    direction="left",
                )
                pygame.draw.line(
                    surface,
                    with_alpha(glyph_color, alpha),
                    (cx + 4, cy + dy - 8),
                    (cx + 4, cy + dy + 8),
                    2,
                )
            else:
                blit_text(
                    surface,
                    self.font(30, True),
                    label,
                    glyph_color,
                    center=(cx, cy + dy),
                    alpha=alpha,
                )

        if self._cooldown > 0.0:
            blit_text(
                surface,
                self.font(12),
                "keypad locked",
                pal.text_muted,
                center=(self._focus_x(), self._hint_y() + dy),
                alpha=int(alpha * 0.85),
                tracking=2,
            )
        else:
            blit_text(
                surface,
                self.font(12),
                "type digits or tap the keypad",
                pal.text_muted,
                center=(self._focus_x(), self._hint_y() + dy),
                alpha=int(alpha * 0.75),
            )

    def _draw_open_prompt(self, surface: pygame.Surface, pal: "Palette", dy: int, alpha: int) -> None:
        prompt_y = 0.50 if current_layout.is_landscape else 0.433
        label_y = 0.61 if current_layout.is_landscape else 0.517
        draw_lock_glyph(
            surface,
            (self._focus_x(), self._focus_y(prompt_y) + dy),
            with_alpha(pal.text_secondary, alpha),
            open_shackle=True,
            scale=1.6,
            background=pal.bg_top,
        )
        blit_text(
            surface,
            self.font(15),
            "SWIPE UP TO UNLOCK",
            pal.text_secondary,
            center=(self._focus_x(), self._focus_y(label_y) + dy),
            alpha=alpha,
            tracking=5,
        )

        for index in range(3):
            phase = (self._elapsed_local * 46.0 + index * 16.0) % 48.0
            y = self._focus_y(0.66 if current_layout.is_landscape else 0.563) + dy - phase
            fade = int(alpha * (1.0 - phase / 48.0))
            if fade <= 4:
                continue
            layer = pygame.Surface((44, 22), pygame.SRCALPHA)
            draw_chevron(layer, (22, 14), with_alpha(pal.accent, fade), size=8, thickness=3, direction="up")
            surface.blit(layer, layer.get_rect(center=(self._focus_x(), int(y))))

        blit_text(
            surface,
            self.font(12),
            "no PIN configured",
            pal.text_muted,
            center=(self._focus_x(), self._hint_y() + dy),
            alpha=int(alpha * 0.8),
            tracking=2,
        )
