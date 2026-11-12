"""Display manager: window modes, scaling, orientation and input mapping.

The logical canvas is independent of the OS window. ``Display`` owns:

* the virtual canvas (portrait 540x1200 or landscape 1200x540),
* the SDL window (windowed / borderless / exclusive, resizable or fullscreen),
* four scaling modes (fit / stretch / integer / fill),
* optional procedural device bezel,
* a single conversion point between window pixels and virtual pixels.

Only :meth:`Display.present` touches the window. Resizing never reallocates the
canvas, and the scaled frame surface is rebuilt only when the destination size
actually changes.
"""

from __future__ import annotations

import warnings
from typing import Callable, Sequence

import pygame

PORTRAIT_SIZE = (540, 1200)
LANDSCAPE_SIZE = (1200, 540)
VIRTUAL_WIDTH, VIRTUAL_HEIGHT = PORTRAIT_SIZE

MIN_WINDOW_SIZE = (320, 480)
MAX_WINDOW_SIZE = (2160, 4800)
RESOLUTION_PRESETS = ((540, 1200), (720, 1600), (1080, 2400), (1440, 3200))

WINDOW_MODES = ("windowed", "borderless", "fullscreen")
SCALING_MODES = ("fit", "stretch", "integer", "fill")
ORIENTATION_MODES = ("portrait", "landscape", "auto")
REFRESH_TARGETS = (30, 60, 120)

FONT_STACK = (
    "Segoe UI",
    "Helvetica Neue",
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
    "FreeSans",
    "Noto Sans",
)

MONO_FONT_STACK = (
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Menlo",
    "Courier New",
    "monospace",
)

WindowResizeListener = Callable[[tuple[int, int], tuple[int, int]], None]


def clamp_window_size(size: Sequence[float]) -> tuple[int, int]:
    width = int(round(float(size[0])))
    height = int(round(float(size[1])))
    width = max(MIN_WINDOW_SIZE[0], min(MAX_WINDOW_SIZE[0], width))
    height = max(MIN_WINDOW_SIZE[1], min(MAX_WINDOW_SIZE[1], height))
    return (width, height)


class Display:
    """Owns the window, the virtual canvas and window <-> virtual conversion."""

    def __init__(
        self,
        title: str = "Phone Simulator",
        virtual_size: tuple[int, int] = PORTRAIT_SIZE,
        *,
        scale: float | None = None,
        resizable: bool = True,
        background: tuple[int, int, int] = (2, 3, 6),
        window_mode: str = "windowed",
        scaling_mode: str = "fit",
        orientation: str = "portrait",
        window_size: tuple[int, int] | None = None,
        device_frame: bool = False,
        vsync: bool = False,
    ) -> None:
        if not pygame.display.get_init():
            pygame.display.init()
        if not pygame.font.get_init():
            pygame.font.init()

        self._title = title
        self._background = background
        self._resizable = bool(resizable)
        self._fonts: dict[tuple[int, bool, bool], pygame.font.Font] = {}
        self._mono_fonts: dict[tuple[int, bool], pygame.font.Font] = {}
        self._listeners: list[WindowResizeListener] = []

        self._window_mode = window_mode if window_mode in WINDOW_MODES else "windowed"
        self._scaling_mode = scaling_mode if scaling_mode in SCALING_MODES else "fit"
        self._orientation_mode = orientation if orientation in ORIENTATION_MODES else "portrait"
        self._device_frame = bool(device_frame)
        self._vsync_requested = bool(vsync)
        self.vsync_active = False
        self._windowed_size = clamp_window_size(window_size or self._auto_window_size(scale))
        self._window = self._create_window(self._windowed_size)

        self.virtual_size: tuple[int, int] = (int(virtual_size[0]), int(virtual_size[1]))
        self._canvas = self._make_canvas()
        self._frame_surface = pygame.Surface((1, 1))

        self._scale_x = 1.0
        self._scale_y = 1.0
        self._dest = pygame.Rect(0, 0, *self.virtual_size)
        self._bezel_rect = pygame.Rect(0, 0, 0, 0)
        self._bezel_pad = 0
        self._source_rect = pygame.Rect(0, 0, *self.virtual_size)

        # Establish the requested orientation before the first geometry pass.
        self._apply_orientation(notify=False)
        self._apply_geometry()

        pygame.display.set_caption(self._title)
        pygame.display.set_icon(self._build_icon())

    # -- construction -------------------------------------------------------
    def _auto_window_size(self, scale: float | None) -> tuple[int, int]:
        base = LANDSCAPE_SIZE if self._orientation_mode == "landscape" else PORTRAIT_SIZE
        if scale is not None:
            chosen = float(scale)
        else:
            try:
                info = pygame.display.Info()
                screen_w = info.current_w if info.current_w > 0 else 1280
                screen_h = info.current_h if info.current_h > 0 else 800
            except pygame.error:
                screen_w, screen_h = 1280, 800
            raw = min(screen_w * 0.90 / base[0], screen_h * 0.90 / base[1])
            chosen = float(int(raw)) if raw >= 1.0 else max(0.25, round(raw, 3))
        return (
            max(1, int(round(base[0] * chosen))),
            max(1, int(round(base[1] * chosen))),
        )

    def _mode_flags(self) -> int:
        if self._window_mode == "windowed":
            return pygame.RESIZABLE if self._resizable else 0
        if self._window_mode == "borderless":
            return getattr(pygame, "FULLSCREEN_DESKTOP", pygame.FULLSCREEN | pygame.NOFRAME)
        return pygame.FULLSCREEN

    def _create_window(self, requested: tuple[int, int]) -> pygame.Surface:
        flags = self._mode_flags()
        size = requested if self._window_mode == "windowed" else (0, 0)
        kwargs: dict[str, int] = {}
        if self._vsync_requested:
            kwargs["vsync"] = 1
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                window = pygame.display.set_mode(size, flags, **kwargs)
            self.vsync_active = bool(self._vsync_requested)
        except (pygame.error, TypeError):
            # Not every SDL video backend supports vsync on a software surface.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                window = pygame.display.set_mode(size, flags)
            self.vsync_active = False
        return window

    def _make_canvas(self) -> pygame.Surface:
        try:
            canvas = pygame.Surface(self.virtual_size).convert()
        except pygame.error:
            canvas = pygame.Surface(self.virtual_size)
        canvas.fill(self._background)
        return canvas

    def _build_icon(self) -> pygame.Surface:
        icon = pygame.Surface((32, 32), pygame.SRCALPHA)
        pygame.draw.rect(icon, (10, 14, 24), pygame.Rect(8, 2, 16, 28), border_radius=5)
        pygame.draw.rect(icon, (90, 220, 255), pygame.Rect(8, 2, 16, 28), width=2, border_radius=5)
        pygame.draw.rect(icon, (90, 220, 255), pygame.Rect(13, 7, 6, 10), border_radius=2)
        pygame.draw.circle(icon, (150, 120, 255), (16, 24), 2)
        return icon

    # -- callbacks ----------------------------------------------------------
    def subscribe_resize(self, callback: WindowResizeListener) -> None:
        if callable(callback) and callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe_resize(self, callback: WindowResizeListener) -> None:
        self._listeners = [item for item in self._listeners if item is not callback]

    def _notify_virtual_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        for callback in list(self._listeners):
            callback(old, new)

    # -- settings-facing controls ------------------------------------------
    @property
    def window_mode(self) -> str:
        return self._window_mode

    @property
    def scaling_mode(self) -> str:
        return self._scaling_mode

    @property
    def orientation_mode(self) -> str:
        return self._orientation_mode

    @property
    def device_frame(self) -> bool:
        return self._device_frame

    @property
    def orientation(self) -> str:
        return "landscape" if self.virtual_size[0] > self.virtual_size[1] else "portrait"

    def set_window_mode(self, mode: str, *, size: tuple[int, int] | None = None) -> bool:
        mode = mode if mode in WINDOW_MODES else "windowed"
        if mode == self._window_mode and size is None:
            return False
        if self._window_mode == "windowed":
            self._windowed_size = clamp_window_size(self._window.get_size())
        self._window_mode = mode
        if size is not None:
            self._windowed_size = clamp_window_size(size)
        self._window = self._create_window(self._windowed_size)
        self._apply_orientation()
        self._apply_geometry()
        return True

    def set_scaling_mode(self, mode: str) -> bool:
        mode = mode if mode in SCALING_MODES else "fit"
        if mode == self._scaling_mode:
            return False
        self._scaling_mode = mode
        self._frame_surface = pygame.Surface((1, 1))
        self._apply_geometry()
        return True

    def set_orientation(self, mode: str) -> bool:
        mode = mode if mode in ORIENTATION_MODES else "portrait"
        if mode == self._orientation_mode:
            return False
        self._orientation_mode = mode
        self._apply_orientation()
        self._apply_geometry()
        return True

    def set_device_frame(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if enabled == self._device_frame:
            return False
        self._device_frame = enabled
        self._frame_surface = pygame.Surface((1, 1))
        self._apply_geometry()
        return True

    def set_vsync(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        if enabled == self._vsync_requested:
            return False
        self._vsync_requested = enabled
        self._window = self._create_window(self._windowed_size)
        self._apply_orientation()
        self._apply_geometry()
        return True

    def set_window_size(self, size: tuple[int, int], *, force_windowed: bool = True) -> bool:
        """Exact client size requested by Settings or a window drag."""
        width, height = clamp_window_size(size)
        if self._window_mode != "windowed":
            if not force_windowed:
                self._windowed_size = (width, height)
                return False
            self._window_mode = "windowed"
            self._window = self._create_window((width, height))
            self._windowed_size = (width, height)
        else:
            self._windowed_size = (width, height)
            if self._window.get_size() != (width, height):
                self._window = self._create_window((width, height))
        self._apply_orientation()
        self._apply_geometry()
        return True

    def resize(self, size: tuple[int, int]) -> None:
        """SDL resize and Settings both converge here (via set_window_size)."""
        if self._window_mode != "windowed":
            return
        self.set_window_size(size)


    # -- orientation --------------------------------------------------------
    def _apply_orientation(self, *, notify: bool = True) -> None:
        old = self.virtual_size
        if self._orientation_mode == "portrait":
            target = PORTRAIT_SIZE
        elif self._orientation_mode == "landscape":
            target = LANDSCAPE_SIZE
        else:
            window_w, window_h = self._window.get_size()
            target = LANDSCAPE_SIZE if window_w > window_h else PORTRAIT_SIZE
        if target == self.virtual_size:
            return
        self.virtual_size = target
        self._canvas = self._make_canvas()
        if notify:
            self._notify_virtual_resize(old, target)

    # -- geometry -----------------------------------------------------------
    def _apply_geometry(self) -> None:
        window_w, window_h = self._window.get_size()
        pad = 0
        if self._device_frame:
            pad = max(10, int(round(min(window_w, window_h) * 0.045)))
        self._bezel_pad = pad
        screen_area = pygame.Rect(pad, pad, max(1, window_w - pad * 2), max(1, window_h - pad * 2))
        dest, scale_x, scale_y, source = self._compute_dest(screen_area)
        self._dest = dest
        self._source_rect = source
        self._scale_x = max(scale_x, 1e-6)
        self._scale_y = max(scale_y, 1e-6)
        self._bezel_rect = dest.inflate(pad * 2, pad * 2) if pad else pygame.Rect(0, 0, 0, 0)
        if self._frame_surface.get_size() != dest.size:
            self._frame_surface = pygame.Surface(dest.size)

    def _compute_dest(
        self, area: pygame.Rect
    ) -> tuple[pygame.Rect, float, float, pygame.Rect]:
        virtual_w, virtual_h = self.virtual_size
        full = pygame.Rect(0, 0, virtual_w, virtual_h)
        mode = self._scaling_mode
        if mode == "stretch":
            return (pygame.Rect(area), area.w / max(1, virtual_w), area.h / max(1, virtual_h), full)
        if mode == "fill":
            scale = max(area.w / max(1, virtual_w), area.h / max(1, virtual_h))
            crop_w = min(virtual_w, max(1, int(round(area.w / scale))))
            crop_h = min(virtual_h, max(1, int(round(area.h / scale))))
            crop = pygame.Rect(0, 0, crop_w, crop_h)
            crop.center = full.center
            # Render only the visible centre crop, never an oversized frame.
            return (pygame.Rect(area), scale, scale, crop)
        elif mode == "integer":
            raw = min(area.w / max(1, virtual_w), area.h / max(1, virtual_h))
            scale = float(int(raw)) if raw >= 1.0 else max(0.05, raw)
        else:
            scale = min(area.w / max(1, virtual_w), area.h / max(1, virtual_h))
        width = max(1, int(round(virtual_w * scale)))
        height = max(1, int(round(virtual_h * scale)))
        dest = pygame.Rect(0, 0, width, height)
        dest.center = area.center
        return (dest, scale, scale, full)

    # -- presentation -------------------------------------------------------
    def present(self) -> None:
        """Blit the virtual canvas using the selected scaling mode."""
        dest = self._dest
        if self._source_rect.size == self.virtual_size and dest.size == self.virtual_size:
            frame = self._canvas
        else:
            target = self._frame_surface
            if target.get_size() != dest.size:
                target = pygame.Surface(dest.size)
                self._frame_surface = target
            source = self._canvas
            if self._source_rect != pygame.Rect(0, 0, *self.virtual_size):
                source = self._canvas.subsurface(self._source_rect)
            # Reuse the destination buffer: resizing never allocates per frame.
            pygame.transform.smoothscale(source, dest.size, target)
            frame = target
        self._window.fill(self._background)
        if self._device_frame:
            self._draw_bezel(self._window)
        self._window.blit(frame, dest.topleft)
        pygame.display.flip()

    def _draw_bezel(self, target: pygame.Surface) -> None:
        outer = self._bezel_rect
        if outer.w <= 0 or outer.h <= 0:
            return
        radius = max(10, min(outer.w, outer.h) // 18)
        pygame.draw.rect(target, (9, 12, 20), outer, border_radius=radius)
        pygame.draw.rect(
            target,
            (54, 64, 86),
            outer,
            width=max(1, self._bezel_pad // 8),
            border_radius=radius,
        )
        # Notch / camera island is part of the frame, not the UI.
        notch_w = max(34, int(outer.w * 0.16))
        notch_h = max(8, int(self._bezel_pad * 0.34))
        notch = pygame.Rect(0, 0, notch_w, notch_h)
        notch.midtop = (outer.centerx, outer.y + max(2, self._bezel_pad // 4))
        pygame.draw.rect(target, (6, 8, 14), notch, border_radius=notch_h // 2)
        pygame.draw.circle(
            target,
            (36, 44, 60),
            (notch.centerx + notch_w // 4, notch.centery),
            max(2, notch_h // 4),
        )

    # -- geometry reads -----------------------------------------------------
    @property
    def canvas(self) -> pygame.Surface:
        return self._canvas

    @property
    def window(self) -> pygame.Surface:
        return self._window

    @property
    def scale(self) -> float:
        return self._scale_x

    @property
    def scale_x(self) -> float:
        return self._scale_x

    @property
    def scale_y(self) -> float:
        return self._scale_y

    @property
    def dest_rect(self) -> pygame.Rect:
        return self._dest

    @property
    def window_size(self) -> tuple[int, int]:
        try:
            return self._window.get_size()
        except pygame.error:
            return self._windowed_size

    @property
    def windowed_size(self) -> tuple[int, int]:
        return self._windowed_size

    def to_virtual(self, pos: Sequence[float]) -> tuple[float, float]:
        x = self._source_rect.x + (float(pos[0]) - self._dest.x) / self._scale_x
        y = self._source_rect.y + (float(pos[1]) - self._dest.y) / self._scale_y
        return (x, y)

    def to_virtual_int(self, pos: Sequence[float]) -> tuple[int, int]:
        x, y = self.clamp_virtual(self.to_virtual(pos))
        return (int(round(x)), int(round(y)))

    def clamp_virtual(self, pos: Sequence[float]) -> tuple[float, float]:
        max_x = float(self.virtual_size[0] - 1)
        max_y = float(self.virtual_size[1] - 1)
        x = min(max(float(pos[0]), 0.0), max_x)
        y = min(max(float(pos[1]), 0.0), max_y)
        return (x, y)

    def to_window(self, pos: Sequence[float]) -> tuple[float, float]:
        return (
            self._dest.x + (float(pos[0]) - self._source_rect.x) * self._scale_x,
            self._dest.y + (float(pos[1]) - self._source_rect.y) * self._scale_y,
        )

    # -- fonts --------------------------------------------------------------
    def font(self, size: int, bold: bool = False, italic: bool = False) -> pygame.font.Font:
        key = (int(size), bool(bold), bool(italic))
        cached = self._fonts.get(key)
        if cached is not None:
            return cached
        try:
            made = pygame.font.SysFont(",".join(FONT_STACK), int(size), bold=bold, italic=italic)
        except (pygame.error, OSError):
            made = pygame.font.Font(None, int(size))
        self._fonts[key] = made
        return made

    def mono_font(self, size: int, bold: bool = False) -> pygame.font.Font:
        key = (int(size), bool(bold))
        cached = self._mono_fonts.get(key)
        if cached is not None:
            return cached
        try:
            made = pygame.font.SysFont(",".join(MONO_FONT_STACK), int(size), bold=bold)
        except (pygame.error, OSError):
            made = pygame.font.Font(None, int(size))
        self._mono_fonts[key] = made
        return made

    def __repr__(self) -> str:
        return (
            f"Display(mode={self._window_mode}, scaling={self._scaling_mode}, "
            f"orientation={self._orientation_mode}, virtual={self.virtual_size[0]}x{self.virtual_size[1]}, "
            f"window={self.window_size[0]}x{self.window_size[1]}, scale={self._scale_x:.3f})"
        )
