"""Video: three procedural clips with a real seekable time base.

Frames are rendered with numpy at an internal 24 fps. The app only regenerates
when the playback clock advances by a frame interval, so a 60 fps UI does not
pay for 60 numpy renders. Every generation pass records its cost in the
profiler as ``video``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
import pygame

from apps._kit import ScrollState, draw_button, draw_card, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text

APP_ID = "video"
APP_NAME = "Video"
STORE_FILE = "video.json"
VIDEO_FPS = 24


@dataclass(frozen=True, slots=True)
class VideoSpec:
    video_id: int
    title: str
    subtitle: str
    duration: float
    seed: int


VIDEOS = (
    VideoSpec(1, "Slow Horizon", "Scrolling gradient", 12.0, 7),
    VideoSpec(2, "Particle Field", "Bouncing particles", 15.0, 19),
    VideoSpec(3, "Plasma Bloom", "Interference plasma", 18.0, 31),
)


def _colorise(red: np.ndarray, green: np.ndarray, blue: np.ndarray) -> np.ndarray:
    rgb = np.stack([np.clip(red, 0, 1), np.clip(green, 0, 1), np.clip(blue, 0, 1)], axis=2)
    return (rgb * 255.0).astype(np.uint8)


def render_video_frame(video_id: int, moment: float, size: tuple[int, int] = (320, 180)) -> np.ndarray:
    width, height = max(8, int(size[0])), max(8, int(size[1]))
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    phase = float(moment)
    if video_id == 1:
        band = (yy + phase * 0.09) % 1.0
        red = 0.15 + 0.55 * band + 0.10 * np.sin(xx * 5.0 + phase)
        green = 0.55 - 0.36 * band + 0.16 * np.sin((xx + yy) * 4.0 - phase * 0.6)
        blue = 0.92 - 0.42 * band
    elif video_id == 2:
        red = np.full((height, width), 0.035, dtype=np.float32)
        green = np.full_like(red, 0.06)
        blue = np.full_like(red, 0.12)
        rng = np.random.default_rng(1907)
        for index in range(14):
            base_x, base_y = rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95)
            speed_x, speed_y = rng.uniform(0.06, 0.17), rng.uniform(0.05, 0.14)
            px = abs(((base_x + phase * speed_x) % 2.0) - 1.0)
            py = abs(((base_y + phase * speed_y) % 2.0) - 1.0)
            radius = rng.uniform(0.025, 0.07)
            glow = np.exp(-(((xx - px) ** 2 + (yy - py) ** 2) / (radius * radius)))
            color = rng.uniform(0.25, 0.9, 3)
            red += glow * color[0] * 0.8
            green += glow * color[1] * 0.8
            blue += glow * color[2] * 0.8
    else:
        value = (
            np.sin(xx * 8.0 + phase * 0.9)
            + np.sin(yy * 11.0 - phase * 0.7)
            + np.sin((xx + yy) * 13.0 + phase * 0.45)
            + 0.6 * np.sin(np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 45.0 - phase)
        )
        value = (value + 3.6) / 7.2
        red = 0.5 + 0.5 * np.sin(value * math.tau)
        green = 0.5 + 0.5 * np.sin(value * math.tau + 2.1)
        blue = 0.5 + 0.5 * np.cos(value * math.tau + 4.1)
    return _colorise(red, green, blue)


def _surface(frame: np.ndarray) -> pygame.Surface:
    return pygame.surfarray.make_surface(np.transpose(frame, (1, 0, 2)))


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    screen = rect.inflate(-int(rect.w * 0.40), -int(rect.h * 0.34))
    draw_rounded(surface, screen, fill=pal.bg_top, stroke=pal.accent, radius=4)
    pygame.draw.polygon(
        surface,
        pal.accent,
        [
            (screen.centerx - 5, screen.centery - 8),
            (screen.centerx + 8, screen.centery),
            (screen.centerx - 5, screen.centery + 8),
        ],
    )
    for hole in range(3):
        pygame.draw.circle(surface, pal.stroke, (rect.x + 8, rect.y + 14 + hole * 12), 2)


class VideoApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._selected: int | None = None
        self._playing = False
        self._time = 0.0
        self._speed = 1.0
        self._fullscreen = False
        self._frame: pygame.Surface | None = None
        self._frame_at = -1.0
        self._last_interval = 0.0
        self._volume = 0.7
        self._drag_seek = False
        self._frame_timer = 0.0
        self._list_scroll = ScrollState(step=86)

    # -- lifecycle ------------------------------------------------------------
    def on_launch(self) -> None:
        self._volume = max(0.0, min(1.0, self.ctx.settings.get_float("sound.master_volume", 0.7)))

    def on_pause(self) -> None:
        self._save()

    def on_close(self) -> None:
        self._save()

    def _save(self) -> None:
        self.ctx.settings.set("sound.master_volume", round(self._volume, 3))
        self.ctx.storage.write_json(STORE_FILE, {"volume": round(self._volume, 3)})

    def update(self, dt: float) -> None:
        super().update(dt)
        if self._selected is None:
            return
        video = VIDEOS[self._selected]
        if self._playing:
            self._time = (self._time + dt * self._speed) % video.duration
        self._frame_timer += max(0.0, dt)
        # Internal 24 fps cap, but never more than one numpy frame per UI step.
        if self._frame_timer >= 1.0 / VIDEO_FPS and abs(self._time - self._frame_at) >= 1.0 / (VIDEO_FPS * 2.0):
            self._frame_timer = 0.0
            self._generate_frame()

    def on_resize(self) -> None:
        self._frame = None
        self._frame_at = -1.0

    # -- geometry -------------------------------------------------------------
    def _list_area(self) -> pygame.Rect:
        return pygame.Rect(
            current_layout.margin,
            self.content.y + 34,
            self.content.w - current_layout.margin * 2,
            self.content.h - 48,
        )

    def _video_rect(self) -> pygame.Rect:
        area = self.content
        if self._fullscreen:
            available = pygame.Rect(
                current_layout.margin,
                area.y + 8,
                area.w - current_layout.margin * 2,
                area.h - 120,
            )
        else:
            available = pygame.Rect(
                current_layout.margin,
                area.y + 46,
                area.w - current_layout.margin * 2,
                min(int(area.h * 0.48), 290),
            )
        width = min(available.w, int(available.h * 16 / 9))
        height = int(width * 9 / 16)
        return pygame.Rect(available.centerx - width // 2, available.y, width, height)

    def _seek_rect(self) -> pygame.Rect:
        video = self._video_rect()
        return pygame.Rect(video.x, video.bottom + 20, video.w, 8)

    def _buttons(self) -> dict[str, pygame.Rect]:
        video = self._video_rect()
        y = video.bottom + 48
        if current_layout.is_landscape:
            y = min(self.content.bottom - 54, y)
        width = min(82, (video.w - 30) // 4)
        gap = 8
        return {
            "play": pygame.Rect(video.x, y, width, 36),
            "speed": pygame.Rect(video.x + width + gap, y, width, 36),
            "full": pygame.Rect(video.x + (width + gap) * 2, y, width, 36),
            "back": pygame.Rect(video.x + (width + gap) * 3, y, width, 36),
        }

    def _volume_rect(self) -> pygame.Rect:
        video = self._video_rect()
        y = self._buttons()["play"].bottom + 22
        return pygame.Rect(video.x, y, video.w, 26)

    # -- frame generation -----------------------------------------------------
    def _generate_frame(self) -> None:
        if self._selected is None:
            return
        started = time.perf_counter()
        size = (max(80, self._video_rect().w), max(45, int(self._video_rect().w * 9 / 16)))
        frame = render_video_frame(VIDEOS[self._selected].video_id, self._time, size)
        self._frame = _surface(frame)
        self._frame_at = self._time
        cost = (time.perf_counter() - started) * 1000.0
        if self.ctx.profiler is not None:
            self.ctx.profiler.record_effect("video", cost)

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if self._selected is None:
            if event.kind is EventKind.WHEEL:
                return self._list_scroll.wheel(event)
            if event.kind is EventKind.POINTER_DOWN:
                self._list_scroll.pointer(event, self._list_area())
            if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
                self._list_scroll.pointer(event, self._list_area())
            if event.kind is EventKind.KEY_DOWN:
                if self._list_scroll.key(event):
                    return True
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self._select(0)
                    return True
            if event.kind is not EventKind.TAP:
                return event.kind is EventKind.POINTER_DOWN
            for index, rect in enumerate(self._row_rects()):
                if rect.collidepoint(event.pos):
                    self._select(index)
                    return True
            return False
        video = VIDEOS[self._selected]
        if event.kind is EventKind.KEY_DOWN:
            if event.key == pygame.K_ESCAPE:
                if self._fullscreen:
                    self._fullscreen = False
                else:
                    self._selected = None
                return True
            if event.key == pygame.K_SPACE:
                self._playing = not self._playing
                return True
            if event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                delta = -2.0 if event.key == pygame.K_LEFT else 2.0
                self._seek(self._time + delta)
                return True
            if (event.text or "").lower() == "f":
                self._fullscreen = not self._fullscreen
                self._frame = None
                return True
        if event.kind is EventKind.POINTER_DOWN:
            if self._seek_rect().inflate(0, 16).collidepoint(event.pos):
                self._drag_seek = True
                self._seek_from_x(event.pos[0])
                return True
            if self._volume_rect().collidepoint(event.pos):
                self._volume = max(0.0, min(1.0, (event.pos[0] - self._volume_rect().x) / max(1, self._volume_rect().w)))
                self.ctx.settings.set("sound.master_volume", round(self._volume, 3))
                return True
            return True
        if event.kind is EventKind.DRAG:
            if self._drag_seek:
                self._seek_from_x(event.pos[0])
            elif self._volume_rect().inflate(0, 10).collidepoint(event.pos):
                self._volume = max(0.0, min(1.0, (event.pos[0] - self._volume_rect().x) / max(1, self._volume_rect().w)))
                self.ctx.settings.set("sound.master_volume", round(self._volume, 3))
            return True
        if event.kind in (EventKind.DRAG_END, EventKind.POINTER_UP):
            if self._drag_seek:
                self._drag_seek = False
                self._save()
            return True
        if event.kind is not EventKind.TAP:
            return False
        buttons = self._buttons()
        if buttons["play"].collidepoint(event.pos):
            self._playing = not self._playing
            return True
        if buttons["speed"].collidepoint(event.pos):
            self._speed = 2.0 if self._speed == 1.0 else (1.0 if self._speed == 0.5 else 0.5)
            return True
        if buttons["full"].collidepoint(event.pos):
            self._fullscreen = not self._fullscreen
            self._frame = None
            return True
        if buttons["back"].collidepoint(event.pos):
            self._selected = None
            self._playing = False
            return True
        if self._video_rect().collidepoint(event.pos):
            self._playing = not self._playing
            return True
        return False

    def _seek_from_x(self, x: float) -> None:
        track = self._seek_rect()
        fraction = max(0.0, min(1.0, (float(x) - track.x) / max(1.0, track.w)))
        self._seek(fraction * VIDEOS[self._selected].duration)

    def _seek(self, moment: float) -> None:
        if self._selected is None:
            return
        self._time = max(0.0, min(VIDEOS[self._selected].duration - 0.01, moment))
        self._generate_frame()

    def _select(self, index: int) -> None:
        self._selected = max(0, min(len(VIDEOS) - 1, index))
        self._time = 0.0
        self._playing = True
        self._frame = None
        self._generate_frame()

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        if self._selected is None:
            self._draw_library(surface, pal)
        else:
            self._draw_player(surface, pal)

    def _row_rects(self) -> list[pygame.Rect]:
        area = self._list_area()
        width, height = 90, 68
        columns = 1
        if area.w >= 760:
            columns = 2
        cell_w = (area.w - 10 * (columns - 1)) // columns
        rows = (len(VIDEOS) + columns - 1) // columns
        return [
            pygame.Rect(
                area.x + (index % columns) * (cell_w + 10),
                area.y + (index // columns) * (height + 10) - int(self._list_scroll.offset),
                cell_w,
                height,
            )
            for index in range(len(VIDEOS))
        ]

    def _draw_library(self, surface, pal) -> None:
        blit_text(surface, self.font(10, True), "PROCEDURAL VIDEO  \u00b7  3 CLIPS", pal.text_muted, topleft=(current_layout.margin, self.content.y + 8), tracking=1.6)
        rows = self._row_rects()
        area = self._list_area()
        self._list_scroll.set_metrics(area.h, len(VIDEOS) * 78)
        for index, (video, rect) in enumerate(zip(VIDEOS, rows)):
            if rect.bottom < area.y or rect.y > area.bottom:
                continue
            draw_card(surface, rect, pal, radius=16)
            thumb = pygame.Rect(rect.x + 8, rect.y + 8, 82, 52)
            color = (90 + index * 55, 120 - index * 10, 200 - index * 40)
            pygame.draw.rect(surface, color, thumb, border_radius=6)
            pygame.draw.polygon(surface, pal.on_accent, [(thumb.centerx - 5, thumb.centery - 8), (thumb.centerx + 8, thumb.centery), (thumb.centerx - 5, thumb.centery + 8)])
            blit_text(surface, self.font(13, True), video.title, pal.text, topleft=(thumb.right + 14, rect.y + 14))
            blit_text(surface, self.font(9), video.subtitle, pal.text_muted, topleft=(thumb.right + 14, rect.y + 34))
            blit_text(surface, self.font(9), f"{int(video.duration)}s", pal.accent_ink, midright=(rect.right - 12, rect.centery))
        draw_scrollbar(surface, area, self._list_scroll.offset, len(VIDEOS) * 78, pal)

    def _draw_player(self, surface, pal) -> None:
        video = VIDEOS[self._selected]
        video_rect = self._video_rect()
        if not self._fullscreen:
            blit_text(surface, self.font(14, True), video.title, pal.text, topleft=(current_layout.margin, self.content.y + 10))
            blit_text(surface, self.font(9), "tap video to play/pause", pal.text_muted, midright=(self.content.right - current_layout.margin, self.content.y + 16))
        if self._frame is None or self._frame.get_size() != video_rect.size:
            self._frame = pygame.transform.smoothscale(self._frame or _surface(render_video_frame(video.video_id, self._time, (video_rect.w, video_rect.h))), video_rect.size)
        surface.blit(self._frame, video_rect.topleft)
        pygame.draw.rect(surface, pal.stroke, video_rect, 1, border_radius=8)
        if not self._playing:
            pygame.draw.circle(surface, (0, 0, 0, 120), video_rect.center, 28)
            pygame.draw.polygon(surface, pal.text, [(video_rect.centerx - 8, video_rect.centery - 14), (video_rect.centerx + 16, video_rect.centery), (video_rect.centerx - 8, video_rect.centery + 14)])
        seek = self._seek_rect()
        pygame.draw.rect(surface, pal.track, seek, border_radius=3)
        fraction = self._time / video.duration if video.duration else 0
        pygame.draw.rect(surface, pal.accent, pygame.Rect(seek.x, seek.y, max(2, int(seek.w * fraction)), seek.h), border_radius=3)
        pygame.draw.circle(surface, pal.surface_raised, (seek.x + int(seek.w * fraction), seek.centery), 7)
        blit_text(surface, self.mono(9), f"{int(self._time // 60):02d}:{int(self._time % 60):02d} / {int(video.duration // 60):02d}:{int(video.duration % 60):02d}", pal.text_muted, topleft=(seek.x, seek.bottom + 8))
        buttons = self._buttons()
        draw_button(surface, buttons["play"], "PAUSE" if self._playing else "PLAY", self.font(9, True), pal, fill=pal.accent, color=pal.on_accent, stroke=None, radius=10)
        draw_button(surface, buttons["speed"], f"{self._speed:.1f}x", self.font(9, True), pal, fill=pal.surface_alt, radius=10)
        draw_button(surface, buttons["full"], "FIT" if self._fullscreen else "FULL", self.font(9, True), pal, fill=pal.surface_alt, radius=10)
        draw_button(surface, buttons["back"], "BACK", self.font(9, True), pal, fill=pal.surface, color=pal.text_muted, radius=10)
        volume = self._volume_rect()
        pygame.draw.rect(surface, pal.track, pygame.Rect(volume.x, volume.centery - 2, volume.w, 4), border_radius=2)
        pygame.draw.rect(surface, pal.accent, pygame.Rect(volume.x, volume.centery - 2, int(volume.w * self._volume), 4), border_radius=2)
        pygame.draw.circle(surface, pal.surface_raised, (volume.x + int(volume.w * self._volume), volume.centery), 6)
        blit_text(surface, self.font(8), "VOLUME", pal.text_muted, topleft=(volume.x, volume.y - 10), tracking=1.2)

    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)


__all__ = ["VideoApp", "draw_icon", "VIDEOS", "render_video_frame"]
