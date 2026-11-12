"""Camera: a simulated viewfinder whose captures flow into Photos."""

from __future__ import annotations

import time

import numpy as np
import pygame

from apps._kit import draw_button
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, with_alpha

APP_ID = "camera"
APP_NAME = "Camera"
FILTERS = ("none", "grayscale", "sepia", "invert")
FILTER_LABELS = {"none": "NONE", "grayscale": "MONO", "sepia": "SEPIA", "invert": "NEG"}
FEED_FPS = 15.0


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    body = rect.inflate(-int(rect.w * 0.34), -int(rect.h * 0.44))
    body.centery += 2
    draw_rounded(surface, body, fill=pal.accent, radius=8)
    lens = pygame.Rect(body.centerx - body.h // 3, body.centery - body.h // 3, body.h // 3 * 2, body.h // 3 * 2)
    pygame.draw.circle(surface, pal.bg_top, lens.center, lens.w // 2)
    pygame.draw.circle(surface, pal.accent_ink, lens.center, max(3, lens.w // 3))
    pygame.draw.rect(surface, pal.accent, pygame.Rect(body.x + 8, body.y - 5, body.w // 4, 6), border_radius=3)


class CameraApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._seed = 0xCA3E
        self._now = 0.0
        self._last_feed = -1.0
        self._frame: pygame.Surface | None = None
        self._filter_index = 0
        self._facing = "back"
        self._grid = False
        self._timer_on = False
        self._countdown = 0.0
        self._flash = 0.0
        self._status = ""
        self._status_t = 0.0
        self._captures = 0

    # -- lifecycle ------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        self._now += max(0.0, dt)
        if self._flash > 0.0:
            self._flash = max(0.0, self._flash - dt)
        if self._status_t > 0.0:
            self._status_t = max(0.0, self._status_t - dt)
            if self._status_t == 0.0:
                self._status = ""
        if self._countdown > 0.0:
            self._countdown = max(0.0, self._countdown - dt)
            if self._countdown == 0.0:
                self._capture()
        if self._frame is None or self._now - self._last_feed >= 1.0 / FEED_FPS:
            self._refresh_frame()

    def on_resize(self) -> None:
        self._frame = None
        self._last_feed = -1.0

    # -- feed -----------------------------------------------------------------
    @property
    def _filter(self) -> str:
        return FILTERS[self._filter_index % len(FILTERS)]

    def _viewfinder(self) -> pygame.Rect:
        margin = current_layout.margin
        if current_layout.is_landscape:
            height = max(180, min(self.content.h - 100, int(self.content.w * 0.34)))
            width = int(height * 4 / 3)
            return pygame.Rect(margin, self.content.y + 14, width, height)
        width = self.content.w - margin * 2
        height = int(width * 1.14)
        max_height = int(self.content.h * 0.62)
        if height > max_height:
            height = max_height
            width = int(height / 1.14)
        return pygame.Rect(self.content.centerx - width // 2, self.content.y + 14, width, height)

    def _refresh_frame(self) -> None:
        import time as _time

        started = _time.perf_counter()
        rect = self._viewfinder()
        size = (max(64, rect.w // 2), max(64, rect.h // 2))
        library = getattr(self.ctx, "photos", None)
        if library is None:
            frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        else:
            frame = library.render_feed(
                self._seed,
                self._now,
                size,
                facing=self._facing,
                filter_name=self._filter,
            )
        # The service renders (height, width, 3); pygame wants (width, height).
        self._frame = pygame.surfarray.make_surface(np.transpose(frame, (1, 0, 2)))
        self._last_feed = self._now
        cost = (_time.perf_counter() - started) * 1000.0
        if self.ctx.profiler is not None:
            self.ctx.profiler.record_effect("camera", cost)

    # -- controls -------------------------------------------------------------
    def _controls(self) -> dict[str, pygame.Rect]:
        view = self._viewfinder()
        y = min(self.content.bottom - 58, view.bottom + 16)
        gap = 8
        names = ("flip", "grid", "timer", "filter", "shutter")
        width = (view.w - gap * 4) // 5
        return {
            name: pygame.Rect(view.x + index * (width + gap), y, width, 44)
            for index, name in enumerate(names)
        }

    def _handle_tap(self, pos: tuple[float, float]) -> bool:
        controls = self._controls()
        if controls["shutter"].collidepoint(pos):
            if self._timer_on:
                self._countdown = 3.0
                self._flash = 0.0
            else:
                self._capture()
            return True
        if controls["filter"].collidepoint(pos):
            self._filter_index = (self._filter_index + 1) % len(FILTERS)
            self._frame = None
            return True
        if controls["grid"].collidepoint(pos):
            self._grid = not self._grid
            return True
        if controls["timer"].collidepoint(pos):
            self._timer_on = not self._timer_on
            return True
        if controls["flip"].collidepoint(pos):
            self._facing = "front" if self._facing == "back" else "back"
            self._frame = None
            return True
        return False

    def _capture(self) -> None:
        library = getattr(self.ctx, "photos", None)
        if library is not None:
            record = library.add_capture(self._seed, self._now, self._filter, self._facing)
            self._captures += 1
            self._status = f"Saved {record.get('name', 'photo')}"
            self._status_t = 1.8
        self._flash = 0.22
        self._countdown = 0.0

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_SPACE:
                self._capture()
                return True
            if (event.text or "").lower() == "f":
                self._filter_index = (self._filter_index + 1) % len(FILTERS)
                self._frame = None
                return True
            return False
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        return self._handle_tap(event.pos)

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        view = self._viewfinder()
        if self._frame is None:
            self._refresh_frame()
        if self._frame is not None:
            surface.blit(pygame.transform.smoothscale(self._frame, view.size), view.topleft)
        draw_rounded(surface, view, fill=None, stroke=pal.stroke, width=2, radius=16)
        if self._grid:
            for index in range(1, 3):
                x = view.x + view.w * index // 3
                y = view.y + view.h * index // 3
                pygame.draw.line(surface, pal.stroke_soft, (x, view.y), (x, view.bottom), 1)
                pygame.draw.line(surface, pal.stroke_soft, (view.x, y), (view.right, y), 1)
        blit_text(
            surface,
            self.font(9, True),
            f"{'FRONT' if self._facing == 'front' else 'BACK'}  \u00b7  {FILTER_LABELS[self._filter]}",
            pal.text_secondary,
            center=(view.centerx, view.y + 14),
            tracking=1.2,
        )
        if self._countdown > 0.0:
            blit_text(
                surface,
                self.mono(58, True),
                str(int(self._countdown + 0.99)),
                pal.warning,
                center=view.center,
            )
        controls = self._controls()
        draw_button(surface, controls["flip"], "FLIP", self.font(8, True), pal, fill=pal.surface_alt, radius=12)
        draw_button(surface, controls["grid"], "GRID" if not self._grid else "GRID ON", self.font(8, True), pal, fill=pal.accent_soft if self._grid else pal.surface, radius=12)
        draw_button(surface, controls["timer"], "TIMER" if not self._timer_on else "3s", self.font(8, True), pal, fill=pal.accent_soft if self._timer_on else pal.surface, radius=12)
        draw_button(surface, controls["filter"], FILTER_LABELS[self._filter], self.font(8, True), pal, fill=pal.surface, radius=12)
        draw_button(surface, controls["shutter"], "SHUTTER", self.font(8, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None, radius=12)
        blit_text(
            surface,
            self.font(8),
            "captures land in Photos \u00b7 Space shutter \u00b7 F filter",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 8),
        )
        if self._status:
            draw_rounded(
                surface,
                pygame.Rect(self.content.centerx - 120, self.content.bottom - 40, 240, 24),
                fill=pal.surface_raised,
                stroke=pal.stroke_soft,
                radius=12,
            )
            blit_text(surface, self.font(9, True), self._status, pal.text, center=(self.content.centerx, self.content.bottom - 28))
        if self._flash > 0.0:
            overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            overlay.fill(with_alpha(pal.text, int(210 * min(1.0, self._flash / 0.18))))
            surface.blit(overlay, (0, 0))

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["CameraApp", "draw_icon", "FILTERS"]
