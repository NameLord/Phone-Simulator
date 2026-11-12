"""numpy-driven rendering effects.

Everything here is procedural and cached. The expensive work (wallpapers,
blur, CRT) is throttled or gated so the UI can keep a 60 Hz step:

* Wallpapers are rendered at 1/3 resolution and upscaled, at 20 fps.
* Blur is downscaled, blurred with a separable Gaussian, then upscaled; results
  are cached per consumer for a few frames.
* Displacement smear runs only during scene transitions.
* CRT scanlines + vignette are off by default and report their real cost.

``Effects.stats`` holds exponential moving averages in milliseconds. The F1
debug overlay reads them, so every effect has a measurable price.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

import numpy as np
import pygame

if TYPE_CHECKING:
    from services.settings import SettingsService

WALLPAPERS: tuple[str, ...] = (
    "flow",
    "plasma",
    "constellation",
    "aurora",
    "ocean",
    "forest",
    "grid",
    "rings",
    "sunburst",
    "synthwave",
    "mountains",
)
WALLPAPER_LABELS: dict[str, str] = {
    "flow": "Flow field",
    "plasma": "Plasma",
    "constellation": "Constellation",
    "aurora": "Aurora",
    "ocean": "Ocean",
    "forest": "Forest",
    "grid": "Neon grid",
    "rings": "Rings",
    "sunburst": "Sunburst",
    "synthwave": "Synthwave",
    "mountains": "Mountains",
}
WALLPAPER_SWATCHES: dict[str, tuple[int, int, int]] = {
    "flow": (90, 220, 255),
    "plasma": (240, 110, 190),
    "constellation": (150, 170, 255),
    "aurora": (86, 226, 178),
    "ocean": (60, 150, 235),
    "forest": (90, 180, 120),
    "grid": (255, 90, 180),
    "rings": (255, 186, 84),
    "sunburst": (255, 150, 70),
    "synthwave": (190, 90, 255),
    "mountains": (120, 150, 210),
}
WALLPAPER_CATEGORIES: dict[str, tuple[str, ...]] = {
    "Fluid": ("flow", "plasma", "aurora", "ocean"),
    "Geometric": ("constellation", "grid", "rings", "sunburst", "synthwave"),
    "Scenic": ("forest", "mountains"),
}

WALLPAPER_INTERVAL = 1.0 / 20.0
BLUR_SCALE = 6


class Effects:
    """Wallpapers, Gaussian blur, transition smear and CRT post-processing."""

    def __init__(self, settings: "SettingsService", size: tuple[int, int]) -> None:
        self._settings = settings
        self.size = (int(size[0]), int(size[1]))
        # 1/5 resolution: ~26k pixels per wall update.  At 20 updates/sec the
        # generator + upscale stays inside a single 60 fps frame budget.
        self._low_size = (max(60, self.size[0] // 5), max(120, self.size[1] // 5))
        # Constellation is cheap (no per-pixel sine field) but needs sharper
        # dots/lines than the flow wallpapers, so it gets its own buffer.
        self._const_size = (max(60, self.size[0] // 3), max(120, self.size[1] // 3))

        self._surface = pygame.Surface(self.size)
        self._surface.fill((0, 0, 0))
        self._low = pygame.Surface(self._low_size)
        self._low_bg = pygame.Surface(self._low_size)
        self._const = pygame.Surface(self._const_size)
        self._const_bg = pygame.Surface(self._const_size)
        self._quarter = pygame.Surface((max(2, self.size[0] // 4), max(2, self.size[1] // 4)))
        self._displaced = pygame.Surface(self.size)
        self._base_array: np.ndarray | None = None

        self._elapsed = 0.0
        self._accumulator = 0.0
        self._wallpaper = ""
        self._palette_key: tuple[Any, ...] | None = None
        self._settings_key: tuple[Any, ...] | None = None
        self._last_unlock_roll = -999.0
        self._bg_top = (12, 16, 30)
        self._bg_bottom = (3, 4, 8)
        self._accent = (90, 220, 255)
        self._accent_2 = (150, 120, 255)
        self._dark = True

        self._rng = np.random.default_rng(0x0A11)
        self._stars = self._new_stars(34)

        self._blur_cache: dict[str, tuple[pygame.Surface, float]] = {}
        self._blur_buffers: dict[str, tuple[pygame.Surface, pygame.Surface]] = {}
        self._crt_mask: np.ndarray | None = None
        self._crt_mask_size: tuple[int, int] | None = None

        self.stats: dict[str, float] = {
            "wallpaper": 0.0,
            "blur": 0.0,
            "smear": 0.0,
            "crt": 0.0,
        }

        self._build_low_background()
        self._wallpaper = self._read_wallpaper()
        self._settings_key = self._read_settings_key()
        self._regenerate()

    # -- canvas lifecycle -----------------------------------------------------
    def resize(self, size: tuple[int, int]) -> None:
        """Rework every cached surface after an orientation change."""
        new_size = (int(size[0]), int(size[1]))
        if new_size == self.size:
            return
        self.size = new_size
        self._low_size = (max(60, new_size[0] // 5), max(120, new_size[1] // 5))
        self._const_size = (max(60, new_size[0] // 3), max(120, new_size[1] // 3))
        self._surface = pygame.Surface(self.size)
        self._surface.fill((0, 0, 0))
        self._low = pygame.Surface(self._low_size)
        self._low_bg = pygame.Surface(self._low_size)
        self._const = pygame.Surface(self._const_size)
        self._const_bg = pygame.Surface(self._const_size)
        self._quarter = pygame.Surface(
            (max(2, self.size[0] // 4), max(2, self.size[1] // 4))
        )
        self._displaced = pygame.Surface(self.size)
        self._base_array = None
        self._blur_cache.clear()
        self._blur_buffers.clear()
        self._crt_mask = None
        self._crt_mask_size = None
        self._palette_key = None
        self._build_low_background()
        self._regenerate()

    # -- settings / palette ---------------------------------------------------
    def _read_wallpaper(self) -> str:
        name = self._settings.get_str("display.wallpaper", "flow")
        return name if name in WALLPAPERS else WALLPAPERS[0]

    def _read_settings_key(self) -> tuple[Any, ...]:
        return (
            self._read_wallpaper(),
            round(float(self._settings.get_float("display.wallpaper_speed", 1.0)), 3),
            round(float(self._settings.get_float("display.wallpaper_density", 1.0)), 3),
            int(self._settings.get_int("display.wallpaper_seed", 137)),
            str(self._settings.get_str("display.wallpaper_palette", "theme")),
            str(self._settings.get_str("display.wallpaper_color1", "#5adcff")),
            str(self._settings.get_str("display.wallpaper_color2", "#7f78ff")),
            bool(self._settings.get_bool("display.power_saver", False)),
            bool(self._settings.get_bool("display.wallpaper_power_saver_disable", True)),
        )

    @property
    def wallpaper_name(self) -> str:
        return self._wallpaper

    @property
    def _speed(self) -> float:
        return max(0.1, min(3.0, float(self._settings.get_float("display.wallpaper_speed", 1.0))))

    @property
    def _density(self) -> float:
        return max(0.1, min(2.0, float(self._settings.get_float("display.wallpaper_density", 1.0))))

    @property
    def _seed(self) -> int:
        return int(self._settings.get_int("display.wallpaper_seed", 137))

    @property
    def _custom_colors(self) -> tuple[tuple[int, int, int], tuple[int, int, int]] | None:
        if self._settings.get_str("display.wallpaper_palette", "theme") != "custom":
            return None
        def parse(value: str, fallback):
            try:
                text = str(value).lstrip("#")
                if len(text) == 3:
                    text = "".join(ch * 2 for ch in text)
                return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
            except (ValueError, IndexError):
                return fallback
        return (
            parse(self._settings.get_str("display.wallpaper_color1", "#5adcff"), self._accent),
            parse(self._settings.get_str("display.wallpaper_color2", "#7f78ff"), self._accent_2),
        )

    @property
    def crt_enabled(self) -> bool:
        return self._settings.get_bool("display.fx_crt", False)

    def set_palette(self, palette: Any, dark: bool) -> None:
        """Accept a ui.theme.Palette without importing the UI layer."""
        key = (
            getattr(palette, "bg_top", self._bg_top),
            getattr(palette, "bg_bottom", self._bg_bottom),
            getattr(palette, "accent", self._accent),
            getattr(palette, "warning", self._accent_2),
            bool(dark),
        )
        if key == self._palette_key:
            return
        self._palette_key = key
        self._bg_top = key[0]
        self._bg_bottom = key[1]
        self._accent = key[2]
        self._accent_2 = key[3]
        self._dark = bool(dark)
        self._build_low_background()
        self._regenerate()

    def _build_low_background(self) -> None:
        width, height = self._low_size
        top = np.array(self._bg_top, dtype=np.float32)
        bottom = np.array(self._bg_bottom, dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :, None]
        gradient = top[None, None, :] * (1.0 - ramp) + bottom[None, None, :] * ramp
        self._base_array = np.repeat(gradient, width, axis=0)
        pygame.surfarray.blit_array(self._low_bg, np.clip(self._base_array, 0, 255).astype(np.uint8))
        pygame.transform.smoothscale(self._low_bg, self._const_size, self._const_bg)

    # -- frame ----------------------------------------------------------------
    def update(self, dt: float, palette: Any, dark: bool) -> None:
        self.set_palette(palette, dark)
        self._elapsed += max(0.0, float(dt))

        settings_key = self._read_settings_key()
        if settings_key != self._settings_key:
            self._settings_key = settings_key
            self._wallpaper = settings_key[0]
            self._accumulator = 0.0
            self._regenerate()

        # Power saver freezes the animated wallpaper on a plain gradient.
        if self._power_saver_static:
            return

        self._accumulator += max(0.0, float(dt))
        if self._accumulator >= WALLPAPER_INTERVAL:
            # One regeneration per frame at most, even after a long pause.
            self._accumulator = 0.0
            self._regenerate()

    @property
    def _power_saver_static(self) -> bool:
        return self._settings.get_bool("display.power_saver", False) and self._settings.get_bool(
            "display.wallpaper_power_saver_disable", True
        )

    def randomize(self) -> str:
        """Pick a new wallpaper and a new variation seed."""
        import random

        name = random.choice(WALLPAPERS)
        self._settings.set("display.wallpaper", name)
        self._settings.set("display.wallpaper_seed", random.randint(0, 999999))
        self._wallpaper = name
        self._settings_key = self._read_settings_key()
        self._accumulator = WALLPAPER_INTERVAL
        self._regenerate()
        return name

    def randomize_on_unlock(self) -> str | None:
        """Rotate the wallpaper once per unlock when the setting is enabled."""
        if not self._settings.get_bool("display.wallpaper_random_unlock", False):
            return None
        if self._elapsed - self._last_unlock_roll < 4.0:
            return None
        self._last_unlock_roll = self._elapsed
        return self.randomize()

    @property
    def wallpaper_surface(self) -> pygame.Surface:
        return self._surface

    def draw_wallpaper(self, target: pygame.Surface) -> None:
        target.blit(self._surface, (0, 0))

    def draw_blurred_wallpaper(self, target: pygame.Surface, radius: int = 14) -> None:
        target.blit(self.blur(self._surface, radius, key="lockscreen", min_interval=0.10), (0, 0))

    # -- wallpaper generators -------------------------------------------------
    def _regenerate(self) -> None:
        start = time.perf_counter()
        if self._power_saver_static:
            pygame.transform.smoothscale(self._low_bg, self.size, self._surface)
            self._record("wallpaper", start)
            return
        if self._wallpaper == "flow":
            self._generate_flow()
            pygame.transform.smoothscale(self._low, self.size, self._surface)
        elif self._wallpaper == "plasma":
            self._generate_plasma()
            pygame.transform.smoothscale(self._low, self.size, self._surface)
        elif self._wallpaper == "constellation":
            self._generate_constellation()
            pygame.transform.smoothscale(self._const, self.size, self._surface)
        elif self._wallpaper == "aurora":
            self._generate_aurora()
        elif self._wallpaper == "ocean":
            self._generate_ocean()
        elif self._wallpaper == "forest":
            self._generate_forest()
        elif self._wallpaper == "grid":
            self._generate_grid()
        elif self._wallpaper == "rings":
            self._generate_rings()
        elif self._wallpaper == "sunburst":
            self._generate_sunburst()
        elif self._wallpaper == "synthwave":
            self._generate_synthwave()
        elif self._wallpaper == "mountains":
            self._generate_mountains()
        else:
            self._generate_flow()
            pygame.transform.smoothscale(self._low, self.size, self._surface)
        self._record("wallpaper", start)

    def _colors(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        custom = self._custom_colors
        accent = np.array((custom[0] if custom else self._accent), dtype=np.float32)
        accent2 = np.array((custom[1] if custom else self._accent_2), dtype=np.float32)
        base = np.array(pygame.surfarray.array3d(self._const_bg), dtype=np.float32)
        return base, accent, accent2, np.array(self._bg_bottom, dtype=np.float32)

    def _write_const(self) -> None:
        pygame.transform.smoothscale(self._const, self.size, self._surface)

    def _generate_aurora(self) -> None:
        width, height = self._const_size
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]
        t = self._elapsed * self._speed
        density = self._density
        base, accent, accent2, _ = self._colors()
        bands = np.zeros((width, height), dtype=np.float32)
        for index in range(4):
            center = 0.22 + index * 0.14 + 0.08 * np.sin(x * (3.0 + index) * density + t * 0.45 + index)
            width_band = 0.10 / density
            bands = np.maximum(bands, np.exp(-((y - center) ** 2) / (2.0 * width_band ** 2)))
        ribbons = 0.5 + 0.5 * np.sin(x * 17.0 * density - t * 1.7 + np.sin(y * 7.0 + t * 0.3))
        mix = (bands * (0.55 + 0.45 * ribbons))[:, :, None]
        tint = accent[None, None, :] * (1.0 - y[:, :, None]) + accent2[None, None, :] * y[:, :, None]
        image = base * (1.0 - 0.55 * mix) + tint * (0.55 * mix)
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_ocean(self) -> None:
        width, height = self._const_size
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]
        t = self._elapsed * self._speed
        base, accent, accent2, _ = self._colors()
        waves = np.zeros((width, height), dtype=np.float32)
        for index in range(3):
            wave = np.sin(x * (8.0 + index * 3.0) * self._density + y * (4.0 + index) + t * (0.9 + index * 0.3)) * 0.5 + 0.5
            waves += wave / (index + 1)
        waves /= 1.83
        depth = np.clip(y * 1.3, 0.0, 1.0)
        tint = accent[None, None, :] * (1.0 - depth[:, :, None]) + accent2[None, None, :] * depth[:, :, None]
        image = base * (0.72 + 0.28 * waves[:, :, None]) + tint * (0.24 * waves[:, :, None])
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_forest(self) -> None:
        width, height = self._const_size
        rng = np.random.default_rng(self._seed & 0xFFFFFFFF)
        base, accent, accent2, _ = self._colors()
        layer = np.array(pygame.surfarray.array3d(self._const_bg), dtype=np.float32)
        beams = max(8, int(18 * self._density))
        for index in range(beams):
            x = rng.uniform(0.0, 1.0)
            width_beam = rng.uniform(0.004, 0.016)
            darkness = rng.uniform(0.20, 0.55)
            column = np.exp(-((np.linspace(0, 1, width, dtype=np.float32) - x) ** 2) / (2 * width_beam ** 2))
            layer *= 1.0 - darkness * column[:, None, None]
        light = np.exp(-((np.linspace(0, 1, height, dtype=np.float32)[None, :] - 0.18) ** 2) / 0.03)
        layer += accent[None, None, :] * light[:, :, None] * 0.26 + accent2[None, None, :] * (1 - light[:, :, None]) * 0.05
        pygame.surfarray.blit_array(self._const, np.clip(layer, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_grid(self) -> None:
        width, height = self._const_size
        base, accent, accent2, _ = self._colors()
        self._const.fill((0, 0, 0))
        image = np.array(base, dtype=np.float32)
        t = self._elapsed * self._speed
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]
        lines = (np.abs(np.sin((x * 14.0 + t * 0.4) * self._density)) < 0.035).astype(np.float32)
        lines = lines + (np.abs(np.sin((y * 18.0 - t * 0.7) * self._density)) < 0.035).astype(np.float32)
        image = image * 0.6 + accent[None, None, :] * np.clip(lines, 0, 1)[:, :, None] * 0.50
        image += accent2[None, None, :] * np.clip(y - 0.7, 0, 1)[:, :, None] * 0.18
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_rings(self) -> None:
        width, height = self._const_size
        x = np.linspace(-1.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(-1.0, 1.0, height, dtype=np.float32)[None, :]
        base, accent, accent2, _ = self._colors()
        radius = np.sqrt(x ** 2 + y ** 2)
        t = self._elapsed * self._speed
        rings = 0.5 + 0.5 * np.sin(radius * (26.0 * self._density) - t * 1.4)
        image = base * (0.72 + 0.28 * rings[:, :, None])
        image += accent[None, None, :] * np.clip(rings, 0, 1)[:, :, None] * 0.30
        image += accent2[None, None, :] * np.clip(1.0 - radius, 0, 1)[:, :, None] * 0.12
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_sunburst(self) -> None:
        width, height = self._const_size
        x = np.linspace(-1.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(-1.0, 1.0, height, dtype=np.float32)[None, :]
        base, accent, accent2, _ = self._colors()
        angle = np.arctan2(y, x)
        radius = np.sqrt(x ** 2 + y ** 2)
        t = self._elapsed * self._speed
        rays = 0.5 + 0.5 * np.sin(angle * (20 * self._density) + t * 0.25)
        centre = np.clip(1.0 - radius * 1.3, 0.0, 1.0)
        image = base * (0.78 + 0.22 * rays[:, :, None]) + accent[None, None, :] * (0.34 * centre[:, :, None])
        image += accent2[None, None, :] * (0.08 * rays[:, :, None] * centre[:, :, None])
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_synthwave(self) -> None:
        width, height = self._const_size
        base, accent, accent2, _ = self._colors()
        x = np.linspace(-1.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(-1.0, 1.0, height, dtype=np.float32)[None, :]
        t = self._elapsed * self._speed
        sun = np.exp(-((np.sqrt((x - 0.0) ** 2) ** 2 + (np.maximum(0, y + 0.15)) ** 2)) / 0.10)
        horizon = np.clip((y + 0.05) * 5.0, 0.0, 1.0)
        grid_x = (np.abs(np.sin((x * 18.0 + t * 0.3) * self._density)) < 0.04).astype(np.float32)
        grid_y = (np.abs(np.sin(((1.0 / (np.abs(y) + 0.08)) * 2.4 - t) * self._density)) < 0.10).astype(np.float32)
        ground = horizon[:, :, None] * (grid_x + grid_y)[:, :, None]
        image = base * (0.62 + 0.38 * horizon[:, :, None]) + accent[None, None, :] * (sun[:, :, None] * 0.55)
        image += accent2[None, None, :] * np.clip(ground, 0, 1) * 0.55
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _generate_mountains(self) -> None:
        width, height = self._const_size
        rng = np.random.default_rng(self._seed & 0xFFFFFFFF)
        base, accent, accent2, _ = self._colors()
        image = np.array(pygame.surfarray.array3d(self._const_bg), dtype=np.float32)
        layers = 4
        for layer_index in range(layers):
            darkness = 0.18 + layer_index * 0.16
            points = []
            ridge = 0.42 + layer_index * 0.13
            for step in range(9):
                x = step / 8.0
                height_point = ridge + rng.uniform(-0.11, 0.11) / self._density
                points.append((x, height_point))
            xs = np.linspace(0.0, 1.0, width)
            ys = np.interp(xs, [point[0] for point in points], [point[1] for point in points])
            for column_index, ridge_y in enumerate(ys):
                top = int(np.clip(ridge_y, 0.0, 1.0) * (height - 1))
                image[column_index, top:, :] *= 1.0 - darkness
                image[column_index, top:, :] += accent2 * 0.025
        sky = np.clip(1.0 - np.linspace(0.0, 1.0, height, dtype=np.float32), 0.0, 1.0)
        image += accent[None, None, :] * (sky[None, :, None] ** 2.2) * 0.22
        pygame.surfarray.blit_array(self._const, np.clip(image, 0, 255).astype(np.uint8))
        self._write_const()

    def _gradient(self) -> np.ndarray:
        if self._base_array is None or self._base_array.shape[:2] != self._low_size:
            self._build_low_background()
        assert self._base_array is not None
        return self._base_array

    def _write_low(self, image: np.ndarray) -> None:
        pygame.surfarray.blit_array(self._low, np.clip(image, 0, 255).astype(np.uint8))

    def _generate_flow(self) -> None:
        """Advect colour along the gradient of a moving stream function."""
        width, height = self._low_size
        t = self._elapsed * self._speed
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]

        # Stream function psi: two crossing waves plus a diagonal ripple. The
        # velocity field is its perpendicular gradient, so the colour bands
        # stretch and fold like a slow fluid. Derivatives are analytic so we
        # avoid a second full-array pass with np.gradient.
        tau = math.tau
        wave_a = x * 3.0 * tau + t * 0.55
        wave_b = y * 2.0 * tau - t * 0.40
        wave_c = (x + y) * 4.0 * tau + t * 0.9
        d_psi_dx = 3.0 * tau * np.cos(wave_a) * np.cos(wave_b) + 0.45 * 4.0 * tau * np.cos(wave_c)
        d_psi_dy = -2.0 * tau * np.sin(wave_a) * np.sin(wave_b) + 0.45 * 4.0 * tau * np.cos(wave_c)

        # Using the velocity components directly avoids atan2/hypot. The
        # coefficients are deliberately small: raw derivatives are large, and
        # high frequencies read as noise rather than a flow.
        band = 0.5 + 0.5 * np.sin(d_psi_dx * 0.060 + d_psi_dy * 0.045 + t * 0.7)
        glow = np.clip((np.abs(d_psi_dx) + np.abs(d_psi_dy)) * 0.02, 0.0, 1.0)

        base = self._gradient()
        accent = np.array(self._accent, dtype=np.float32)
        mix = (band * 0.65 + glow * 0.35)[:, :, None]
        image = base * (1.0 - 0.18 * mix) + accent * (0.32 * mix)
        self._write_low(image)

    def _generate_plasma(self) -> None:
        """Classic four-sine plasma, mapped into the accent palette."""
        width, height = self._low_size
        t = self._elapsed * self._speed
        x = np.linspace(0.0, 1.0, width, dtype=np.float32)[:, None]
        y = np.linspace(0.0, 1.0, height, dtype=np.float32)[None, :]

        value = (
            np.sin(x * 8.0 + t * 1.1)
            + np.sin(y * 7.0 - t * 0.8)
            + np.sin((x + y) * 5.0 + t * 0.6)
            + np.sin(np.hypot(x - 0.5, y - 0.5) * 16.0 - t * 1.4)
        )
        value = (value + 4.0) / 8.0

        base = self._gradient()
        accent = np.array(self._accent, dtype=np.float32)
        secondary = np.array((255.0 - self._accent[0], 110.0, 255.0 - self._accent[2]), dtype=np.float32)
        first = 0.5 + 0.5 * np.sin(value * math.tau + t * 0.4)
        second = 0.5 + 0.5 * np.cos(value * math.tau * 1.3 - t * 0.3)
        tint = (accent[None, None, :] * first[:, :, None] + secondary[None, None, :] * second[:, :, None]) * 0.5
        image = base * 0.55 + tint * 0.45
        self._write_low(image)

    def _new_stars(self, count: int) -> np.ndarray:
        stars = np.zeros((count, 5), dtype=np.float32)
        stars[:, 0] = self._rng.random(count)
        stars[:, 1] = self._rng.random(count)
        stars[:, 2] = (self._rng.random(count) - 0.5) * 0.018
        stars[:, 3] = (self._rng.random(count) - 0.5) * 0.014
        stars[:, 4] = self._rng.random(count) * 0.4 + 0.6
        return stars

    def _generate_constellation(self) -> None:
        """Drifting points with numpy distance tests; bright pairs are linked."""
        width, height = self._const_size
        self._stars[:, 0] = (self._stars[:, 0] + self._stars[:, 2] * 0.05) % 1.0
        self._stars[:, 1] = (self._stars[:, 1] + self._stars[:, 3] * 0.05) % 1.0

        self._const.blit(self._const_bg, (0, 0))
        accent = self._accent
        line_color = tuple(int(channel * 0.45 + 60) for channel in accent)
        point_color = tuple(min(255, int(channel * 0.6 + 90)) for channel in accent)

        px = self._stars[:, 0] * width
        py = self._stars[:, 1] * height
        dx = px[:, None] - px[None, :]
        dy = py[:, None] - py[None, :]
        distance = np.hypot(dx, dy)
        threshold = min(width, height) * 0.20
        upper = np.triu(distance < threshold, k=1)
        for first, second in zip(*np.nonzero(upper)):
            pygame.draw.line(
                self._const,
                line_color,
                (int(px[first]), int(py[first])),
                (int(px[second]), int(py[second])),
                1,
            )
        twinkle = 0.65 + 0.35 * np.sin(self._elapsed * 2.0 + np.arange(len(px)))
        for index, (x, y) in enumerate(zip(px, py)):
            radius = 1 if self._stars[index, 4] < 0.8 else 2
            pygame.draw.circle(
                self._const,
                point_color,
                (int(x), int(y)),
                radius + int(twinkle[index] > 0.9),
            )
        self._last_constellation = time.perf_counter()

    # -- Gaussian blur --------------------------------------------------------
    def blur(
        self,
        source: pygame.Surface,
        radius: int = 12,
        *,
        key: str = "default",
        min_interval: float = 0.0,
    ) -> pygame.Surface:
        """Downscale -> separable Gaussian -> upscale, cached per consumer.

        The 1D Gaussian is built once per call (a few entries). A separable
        convolution applies the horizontal kernel to every row, then the
        vertical kernel to every column, which is O(2k) passes instead of the
        O(k^2) a 2D kernel would need.
        """
        if self._settings.get_bool("accessibility.reduce_motion", False):
            return source
        now = time.perf_counter()
        cached = self._blur_cache.get(key)
        if cached is not None and now - cached[1] < min_interval:
            return cached[0]

        start = time.perf_counter()
        small_size = (
            max(8, source.get_width() // BLUR_SCALE),
            max(8, source.get_height() // BLUR_SCALE),
        )
        buffers = self._blur_buffers.get(key)
        if buffers is None or buffers[0].get_size() != small_size or buffers[1].get_size() != self.size:
            small = pygame.Surface(small_size)
            full = pygame.Surface(self.size)
            self._blur_buffers[key] = (small, full)
        else:
            small, full = buffers
        # Nearest downscale is fine here: the Gaussian removes the aliasing,
        # and it is ~30x cheaper than smoothscale on the full canvas.
        pygame.transform.scale(source, small_size, small)

        array = pygame.surfarray.array3d(small).astype(np.float32)
        # Kernel radius also shrinks with the downscale, so a 12 px screen blur
        # is ~2 px on the small image and still looks broad after upscaling.
        kernel_radius = max(1, int(radius / (BLUR_SCALE / 2)))
        sigma = max(0.8, kernel_radius / 2.0)
        offsets = np.arange(-kernel_radius, kernel_radius + 1, dtype=np.float32)
        kernel = np.exp(-(offsets * offsets) / (2.0 * sigma * sigma))
        kernel /= kernel.sum()

        height = array.shape[1]
        pad = kernel_radius
        padded = np.pad(array, ((pad, pad), (0, 0), (0, 0)), mode="edge")
        horizontal = np.zeros_like(array)
        for index, weight in enumerate(kernel):
            horizontal += padded[index : index + array.shape[0], :, :] * weight

        padded = np.pad(horizontal, ((0, 0), (pad, pad), (0, 0)), mode="edge")
        blurred = np.zeros_like(array)
        for index, weight in enumerate(kernel):
            blurred += padded[:, index : index + height, :] * weight

        pygame.surfarray.blit_array(small, np.clip(blurred, 0, 255).astype(np.uint8))
        pygame.transform.smoothscale(small, self.size, full)
        self._blur_cache[key] = (full, now)
        self._record("blur", start)
        return full

    # -- transition smear -----------------------------------------------------
    def displace(self, target: pygame.Surface, strength: float, phase: float = 0.0) -> None:
        """Row-wise horizontal displacement used during scene transitions."""
        strength = max(0.0, min(1.0, float(strength)))
        if strength <= 0.002:
            return
        start = time.perf_counter()
        quarter_size = self._quarter.get_size()
        pygame.transform.smoothscale(target, quarter_size, self._quarter)
        array = pygame.surfarray.pixels3d(self._quarter)
        width, height = array.shape[0], array.shape[1]
        xs = np.arange(width, dtype=np.int32)[:, None]
        ys = np.arange(height, dtype=np.int32)[None, :]
        # A travelling sine gives a soft shear; the amplitude is in quarter-res
        # pixels, so it reads as ~24 px at the end of the transition.
        shift = (strength * 6.0 * np.sin(ys * 0.045 + phase * 6.0)).astype(np.int32)
        indices = (xs - shift) % width
        displaced = array[indices, ys, :]
        del array
        pygame.surfarray.blit_array(self._quarter, displaced)
        pygame.transform.smoothscale(self._quarter, self.size, self._displaced)
        target.blit(self._displaced, (0, 0))
        self._record("smear", start)

    # -- CRT post-pass --------------------------------------------------------
    def _ensure_crt_mask(self) -> np.ndarray:
        if self._crt_mask is not None and self._crt_mask_size == self.size:
            return self._crt_mask
        width, height = self.size
        x = (np.arange(width, dtype=np.float32) / max(1, width - 1) - 0.5) * 2.0
        y = (np.arange(height, dtype=np.float32) / max(1, height - 1) - 0.5) * 2.0
        # Elliptical vignette: 1 at the centre, ~0.55 in the corners.
        radius2 = x[:, None] ** 2 * 0.55 + y[None, :] ** 2
        vignette = np.clip(1.0 - 0.34 * radius2, 0.55, 1.0)
        scanline = np.ones((1, height), dtype=np.float32)
        scanline[0, ::3] = 0.86
        combined = vignette * scanline
        self._crt_mask = np.clip(combined * 256.0, 0, 255).astype(np.uint16)
        self._crt_mask_size = self.size
        return self._crt_mask

    def apply_crt(self, target: pygame.Surface) -> None:
        if not self.crt_enabled:
            return
        start = time.perf_counter()
        mask = self._ensure_crt_mask()
        view = pygame.surfarray.pixels3d(target)
        array = view.astype(np.uint16)
        array *= mask[:, :, None]
        array >>= 8
        view[:] = array.astype(np.uint8)
        del view
        self._record("crt", start)

    # -- accounting -----------------------------------------------------------
    def _record(self, name: str, started: float) -> None:
        cost = (time.perf_counter() - started) * 1000.0
        previous = self.stats.get(name, 0.0)
        self.stats[name] = previous * 0.8 + cost * 0.2 if previous else cost

    def cost_rows(self) -> list[tuple[str, str]]:
        crt = f"{self.stats['crt']:.2f} ms" if self.crt_enabled else "off"
        return [
            ("fx wall", f"{self.stats['wallpaper']:.2f} ms"),
            ("fx blur", f"{self.stats['blur']:.2f} ms"),
            ("fx smear", f"{self.stats['smear']:.2f} ms"),
            ("fx crt", crt),
        ]

    def __repr__(self) -> str:
        return f"Effects(wallpaper={self._wallpaper!r}, size={self.size})"
