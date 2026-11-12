"""Photos: a procedural gallery with eight generators and a zoomable viewer."""

from __future__ import annotations

import math

import numpy as np
import pygame

from apps._kit import ScrollState, draw_button, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import Direction, EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "photos"
APP_NAME = "Photos"
STORE_FILE = "photos.json"
PHOTO_W = 240
PHOTO_H = 320


def _norm(array: np.ndarray) -> np.ndarray:
    low = float(array.min())
    high = float(array.max())
    if high - low < 1e-6:
        return np.zeros_like(array, dtype=np.float32)
    return ((array - low) / (high - low)).astype(np.float32)


def _colorise(red: np.ndarray, green: np.ndarray, blue: np.ndarray) -> np.ndarray:
    rgb = np.stack(
        [np.clip(red, 0.0, 1.0), np.clip(green, 0.0, 1.0), np.clip(blue, 0.0, 1.0)],
        axis=2,
    )
    return (rgb * 255.0).astype(np.uint8)


def _grid(size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    width, height = size
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)
    return np.meshgrid(x, y)


def generate_photo(photo_id: int, size: tuple[int, int] = (PHOTO_W, PHOTO_H)) -> np.ndarray:
    width, height = size
    xx, yy = _grid(size)
    rng = np.random.default_rng(9173 + photo_id * 104729)
    if photo_id == 1:  # gradient
        band = yy
        red = 0.18 + 0.55 * band + 0.12 * np.sin(xx * 7.0)
        green = 0.66 - 0.40 * band
        blue = 0.92 - 0.30 * band
    elif photo_id == 2:  # plasma
        value = (
            np.sin(xx * 12.0 + 1.2)
            + np.sin(yy * 9.0 - 0.8)
            + np.sin((xx + yy) * 14.0)
            + np.sin(np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 36.0)
        )
        value = _norm(value)
        red = value
        green = 0.25 + 0.75 * np.sin(value * math.pi)
        blue = 1.0 - value
    elif photo_id == 3:  # mandelbrot thumbnail
        scale_x = (xx - 0.5) * 2.8 - 0.5
        scale_y = (yy - 0.5) * 2.6
        c = scale_x + 1j * scale_y
        z = np.zeros_like(c)
        count = np.zeros(c.shape, dtype=np.float32)
        mask = np.ones(c.shape, dtype=bool)
        for iteration in range(48):
            z[mask] = z[mask] * z[mask] + c[mask]
            escaped = mask & (np.abs(z) > 2.0)
            count[escaped] = iteration / 48.0
            mask &= ~escaped
            if not mask.any():
                break
        value = _norm(np.where(mask, 0.0, count + 0.08 * np.abs(z)))
        red = np.sin(value * math.tau * 1.1) * 0.5 + 0.5
        green = value
        blue = np.cos(value * math.tau * 0.8) * 0.5 + 0.5
    elif photo_id == 4:  # checkerboard
        checker = ((np.floor(xx * 14) + np.floor(yy * 18)) % 2.0)
        value = 0.25 + 0.55 * checker
        red = value
        green = value * 0.8 + 0.15
        blue = 1.0 - value * 0.7
    elif photo_id == 5:  # noise
        value = rng.random((height, width), dtype=np.float32)
        value = (value + np.roll(value, 1, axis=0) + np.roll(value, 1, axis=1)) / 3.0
        red = value
        green = np.roll(value, 3, axis=1)
        blue = np.roll(value, 5, axis=0)
    elif photo_id == 6:  # circles
        cx = 0.5 + 0.15 * np.sin(xx * 3.0)
        cy = 0.5 + 0.12 * np.cos(yy * 4.0)
        distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        rings = 0.5 + 0.5 * np.sin(distance * 68.0)
        red = 0.15 + 0.6 * rings
        green = 0.55 - 0.35 * distance
        blue = 0.45 + 0.5 * rings
    elif photo_id == 7:  # stripes
        stripes = 0.5 + 0.5 * np.sin((xx * 18.0 + yy * 5.0) * math.pi)
        red = stripes * 0.95
        green = 0.25 + 0.35 * np.sin(yy * 4.0)
        blue = 1.0 - stripes * 0.55
    else:  # gradient + noise
        noise = rng.normal(0.0, 0.06, (height, width)).astype(np.float32)
        glow = np.exp(-((xx - 0.35) ** 2 + (yy - 0.30) ** 2) * 9.0)
        red = np.clip(glow + noise + yy * 0.4, 0.0, 1.0)
        green = np.clip(glow * 0.6 + yy * 0.35 + noise, 0.0, 1.0)
        blue = np.clip(0.55 - yy * 0.35 + noise, 0.0, 1.0)
    return _colorise(red, green, blue)


def _surface(array: np.ndarray) -> pygame.Surface:
    return pygame.surfarray.make_surface(np.transpose(array, (1, 0, 2)))


PHOTO_NAMES = (
    "Aurora Drift",
    "Plasma Field",
    "Mandelbrot",
    "Checkered",
    "Grain",
    "Orbits",
    "Interference",
    "Nebula",
)


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    frame = rect.inflate(-int(rect.w * 0.34), -int(rect.h * 0.34))
    draw_rounded(surface, frame, fill=(255, 255, 255), radius=4)
    inner = frame.inflate(-4, -4)
    pygame.draw.rect(surface, pal.accent, inner, border_radius=3)
    pygame.draw.circle(surface, pal.warning, (inner.x + inner.w // 3, inner.y + inner.h // 3), max(2, rect.w // 20))
    pygame.draw.polygon(
        surface,
        pal.success,
        [
            (inner.x + 2, inner.bottom - 2),
            (inner.x + inner.w // 2, inner.y + inner.h // 2),
            (inner.right - 2, inner.bottom - 2),
        ],
    )


class PhotosApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._deleted: set[int] = set()
        self._grid_scroll = ScrollState(step=90)
        self._cell_rects: list[tuple[int, pygame.Rect]] = []
        self._viewer: int | None = None
        self._zoom = False
        self._thumbs: dict[tuple[int, int, int], pygame.Surface] = {}
        self._fulls: dict[int, pygame.Surface] = {}
        self._toast = ""
        self._toast_t = 0.0
        self._loader: int = 0

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._load()

    def on_resume(self) -> None:
        self._load()
        self._thumbs.clear()
        self._fulls.clear()

    def update(self, dt: float) -> None:
        super().update(dt)
        if self._toast_t > 0.0:
            self._toast_t = max(0.0, self._toast_t - dt)
            if self._toast_t == 0.0:
                self._toast = ""

    def on_resize(self) -> None:
        self._thumbs.clear()
        self._grid_scroll.reset()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        library = getattr(self.ctx, "photos", None)
        if library is not None:
            library.reload()
            self._deleted = set(library.deleted)
        else:
            payload = self.ctx.storage.read_json(STORE_FILE, {})
            deleted = payload.get("deleted", []) if isinstance(payload, dict) else []
            if isinstance(deleted, list):
                self._deleted = {int(value) for value in deleted if isinstance(value, (int, float)) and 1 <= int(value) <= len(PHOTO_NAMES)}
        if self._viewer is not None and self._viewer not in self._visible_ids():
            self._viewer = None

    def _save(self) -> None:
        library = getattr(self.ctx, "photos", None)
        if library is not None:
            library.deleted = set(self._deleted)
            library.save()
        else:
            self.ctx.storage.write_json(STORE_FILE, {"deleted": sorted(self._deleted)})

    def _visible_ids(self) -> list[int]:
        ids = [photo_id for photo_id in range(1, len(PHOTO_NAMES) + 1) if photo_id not in self._deleted]
        library = getattr(self.ctx, "photos", None)
        if library is not None:
            ids.extend(int(item["id"]) for item in library.captures)
        return ids

    def _photo_name(self, photo_id: int) -> str:
        if 1 <= photo_id <= len(PHOTO_NAMES):
            return PHOTO_NAMES[photo_id - 1]
        library = getattr(self.ctx, "photos", None)
        if library is not None:
            capture = library.capture(photo_id)
            if capture is not None:
                return str(capture.get("name", f"Capture {photo_id}"))
        return f"Capture {photo_id}"

    # -- surfaces -------------------------------------------------------------
    def _full_surface(self, photo_id: int) -> pygame.Surface:
        surface = self._fulls.get(photo_id)
        if surface is None:
            import time as _time

            started = _time.perf_counter()
            library = getattr(self.ctx, "photos", None)
            if photo_id > len(PHOTO_NAMES) and library is not None:
                capture = library.capture(photo_id)
                if capture is not None:
                    frame = library.render_capture(capture, (PHOTO_W, PHOTO_H))
                    surface = pygame.surfarray.make_surface(np.transpose(frame, (1, 0, 2)))
            if surface is None:
                surface = _surface(generate_photo(photo_id if photo_id <= len(PHOTO_NAMES) else 1))
            self._fulls[photo_id] = surface
            if self.ctx.profiler is not None:
                self.ctx.profiler.record_effect("photos", (_time.perf_counter() - started) * 1000.0)
        return surface

    def _thumb(self, photo_id: int, size: tuple[int, int]) -> pygame.Surface:
        key = (photo_id, int(size[0]), int(size[1]))
        cached = self._thumbs.get(key)
        if cached is not None:
            return cached
        full = self._full_surface(photo_id)
        thumb = pygame.transform.smoothscale(full, (max(1, int(size[0])), max(1, int(size[1]))))
        self._thumbs[key] = thumb
        return thumb

    def _flash(self, text: str) -> None:
        self._toast = text
        self._toast_t = 1.8

    # -- geometry -------------------------------------------------------------
    def _grid_columns(self) -> int:
        if current_layout.is_landscape:
            return 4 if self.content.w >= 1000 else 3
        return 2 if self.content.w < 760 else 3

    def _grid_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 34,
            self.content.w - current_layout.margin * 2,
            self.content.h - 48,
        )

    def _cell_size(self) -> tuple[int, int]:
        area = self._grid_area()
        columns = self._grid_columns()
        gap = 10
        width = (area.w - gap * (columns - 1)) // columns
        return width, int(width * 1.30)

    def _cell_rect(self, index: int) -> pygame.Rect:
        area = self._grid_area()
        columns = self._grid_columns()
        width, height = self._cell_size()
        gap = 10
        row, column = divmod(index, columns)
        return pygame.Rect(
            area.x + column * (width + gap),
            area.y + row * (height + gap) - int(self._grid_scroll.offset),
            width,
            height,
        )

    def _viewer_buttons(self) -> dict[str, pygame.Rect]:
        bottom = self.content.bottom - 54
        width = min(108, (self.content.w - 60) // 3)
        return {
            "back": pygame.Rect(current_layout.margin, self.content.y + 8, 66, 34),
            "share": pygame.Rect(self.content.centerx - width // 2, bottom, width, 40),
            "delete": pygame.Rect(self.content.centerx + width // 2 + 24, bottom, width, 40),
        }

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._viewer is not None:
            return self._handle_viewer(event)
        if event.kind is EventKind.WHEEL:
            return self._grid_scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._grid_scroll.pointer(event, self._grid_area())
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._grid_scroll.pointer(event, self._grid_area())
        if event.kind is EventKind.KEY_DOWN:
            if self._grid_scroll.key(event):
                return True
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                ids = self._visible_ids()
                if ids:
                    self._viewer = ids[0]
                    self._zoom = False
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        for photo_id, rect in self._cell_rects:
            if rect.collidepoint(event.pos):
                self._viewer = photo_id
                self._zoom = False
                return True
        return False

    def open_payload(self, payload: dict) -> None:
        photo_id = payload.get("photo_id")
        if isinstance(photo_id, (int, float)) and int(photo_id) in self._visible_ids():
            self._viewer = int(photo_id)
            self._zoom = False

    def _handle_viewer(self, event: PointerEvent) -> bool:
        ids = self._visible_ids()
        if self._viewer not in ids:
            self._viewer = ids[0] if ids else None
            return True
        index = ids.index(self._viewer)
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                self._viewer = None
            elif event.key in (pygame.K_LEFT, pygame.K_a):
                self._viewer = ids[(index - 1) % len(ids)]
                self._zoom = False
            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                self._viewer = ids[(index + 1) % len(ids)]
                self._zoom = False
            elif event.key == pygame.K_DELETE:
                self._delete_current()
            return True
        if event.kind is EventKind.SWIPE:
            if event.direction is Direction.LEFT:
                self._viewer = ids[(index + 1) % len(ids)]
            elif event.direction is Direction.RIGHT:
                self._viewer = ids[(index - 1) % len(ids)]
            self._zoom = False
            return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        buttons = self._viewer_buttons()
        if buttons["back"].collidepoint(event.pos):
            self._viewer = None
            return True
        if buttons["delete"].collidepoint(event.pos):
            self._delete_current()
            return True
        if buttons["share"].collidepoint(event.pos):
            title = self._photo_name(self._viewer) if self._viewer is not None else "Photo"
            self.ctx.share({"type": "image", "title": title, "photo_id": self._viewer})
            return True
        if self._photo_rect().collidepoint(event.pos):
            self._zoom = not self._zoom
            return True
        return False

    def _delete_current(self) -> None:
        if self._viewer is None:
            return
        library = getattr(self.ctx, "photos", None)
        if self._viewer > len(PHOTO_NAMES) and library is not None:
            library.delete_capture(self._viewer)
        else:
            self._deleted.add(self._viewer)
            if library is not None:
                library.delete_builtin(self._viewer)
            else:
                self._save()
        self._fulls.pop(self._viewer, None)
        ids = self._visible_ids()
        self._viewer = ids[0] if ids else None
        self._zoom = False
        self._flash("Photo removed")

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        if self._viewer is not None:
            self._draw_viewer(surface, pal)
        else:
            self._draw_grid(surface, pal)
        if self._toast:
            draw_rounded(
                surface,
                pygame.Rect(self.content.centerx - 150, self.content.bottom - 34, 300, 26),
                fill=pal.surface_raised,
                stroke=pal.stroke_soft,
                radius=13,
            )
            blit_text(surface, self.font(10, True), self._toast, pal.text, center=(self.content.centerx, self.content.bottom - 21))

    def _draw_grid(self, surface, pal) -> None:
        ids = self._visible_ids()
        blit_text(
            surface,
            self.font(10, True),
            f"GALLERY  \u00b7  {len(ids)} PHOTOS",
            pal.text_muted,
            topleft=(current_layout.margin, self.content.y + 8),
            tracking=1.6,
        )
        if not ids:
            blit_text(surface, self.font(13), "No photos left", pal.text_muted, center=self.content.center)
            return
        area = self._grid_area()
        width, height = self._cell_size()
        columns = self._grid_columns()
        rows = (len(ids) + columns - 1) // columns
        self._grid_scroll.set_metrics(area.h, rows * (height + 10))
        self._cell_rects = []
        for index, photo_id in enumerate(ids):
            cell = self._cell_rect(index)
            if cell.bottom < area.y or cell.y > area.bottom:
                continue
            thumb_rect = pygame.Rect(cell.x, cell.y, cell.w, int(cell.h * 0.78))
            thumb = self._thumb(photo_id, thumb_rect.size)
            surface.set_clip(area)
            surface.blit(thumb, thumb_rect.topleft)
            surface.set_clip(None)
            draw_rounded(surface, thumb_rect, fill=None, stroke=pal.stroke_soft, radius=8)
            blit_text(surface, self.font(10, True), fit_text(self.font(10, True), self._photo_name(photo_id), cell.w - 4), pal.text, topleft=(cell.x + 2, thumb_rect.bottom + 7))
            blit_text(surface, self.font(8), f"CAP-{photo_id:04d}" if photo_id > len(PHOTO_NAMES) else f"PROC-{photo_id:02d}", pal.text_muted, topleft=(cell.x + 2, thumb_rect.bottom + 22))
            self._cell_rects.append((photo_id, cell))
        draw_scrollbar(surface, area, self._grid_scroll.offset, rows * (height + 10), pal)

    def _photo_rect(self) -> pygame.Rect:
        width = min(self.content.w - current_layout.margin * 2, 330)
        height = int(width * 1.33)
        max_height = self.content.h - 130
        if height > max_height:
            height = max_height
            width = int(height / 1.33)
        return pygame.Rect(self.content.centerx - width // 2, self.content.y + 48, width, height)

    def _draw_viewer(self, surface, pal) -> None:
        photo_id = self._viewer
        if photo_id is None:
            return
        ids = self._visible_ids()
        index = ids.index(photo_id) if photo_id in ids else 0
        photo_rect = self._photo_rect()
        full = self._full_surface(photo_id)
        if self._zoom:
            crop_w = int(full.get_width() * 0.52)
            crop_h = int(full.get_height() * 0.52)
            source = pygame.Rect(
                (full.get_width() - crop_w) // 2,
                (full.get_height() - crop_h) // 2,
                crop_w,
                crop_h,
            )
            image = pygame.transform.smoothscale(full.subsurface(source), photo_rect.size)
        else:
            image = pygame.transform.smoothscale(full, photo_rect.size)
        surface.blit(image, photo_rect.topleft)
        draw_rounded(surface, photo_rect, fill=None, stroke=pal.stroke, radius=10)
        blit_text(
            surface,
            self.font(16, True),
            self._photo_name(photo_id),
            pal.text,
            center=(self.content.centerx, photo_rect.bottom + 20),
        )
        blit_text(
            surface,
            self.font(9),
            f"{index + 1} / {len(ids)}  \u00b7  tap to {'fit' if self._zoom else 'zoom'}  \u00b7  swipe to browse",
            pal.text_muted,
            center=(self.content.centerx, photo_rect.bottom + 40),
        )
        buttons = self._viewer_buttons()
        draw_button(surface, buttons["back"], "BACK", self.font(9, True), pal, fill=pal.surface, color=pal.text_muted)
        draw_button(surface, buttons["share"], "SHARE", self.font(10, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None)
        draw_button(surface, buttons["delete"], "DELETE", self.font(10, True), pal, fill=pal.surface_alt, color=pal.danger)
        if self._zoom:
            blit_text(surface, self.font(8), "2.2x", pal.accent_ink, topleft=(photo_rect.right - 34, photo_rect.y + 8))

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["PhotosApp", "draw_icon", "generate_photo", "PHOTO_NAMES"]
