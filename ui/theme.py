"""The one place where colours, typography and appearance live.

``Theme`` is a live projection of the settings store: it reads ``display.mode``,
``display.accent``, ``display.font_scale``, ``display.brightness`` and
``display.power_saver`` every time they are queried, so changing a setting
re-themes every screen on the next frame with no restart and no event plumbing.

Two palettes matter:

* ``DARK_BASE`` / ``LIGHT_BASE`` are the only definitions of UI colours.
* ``SPLASH`` keeps the boot branding constants (fixed by design, still owned
  here so no other module repeats a colour literal).

Accent selection is applied on top of the base palette, so a new accent preset
never needs to duplicate the rest of the palette.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

import pygame

from services.theme_engine import ThemeEngine
from ui.paint import contrast_text, darken, luminance, mix

if TYPE_CHECKING:
    from core.display import Display
    from services.settings import SettingsService

RGB = tuple[int, int, int]

MODE_DARK = "dark"
MODE_LIGHT = "light"
MODES: tuple[str, ...] = (MODE_DARK, MODE_LIGHT)

ACCENTS: dict[str, RGB] = {
    "cyan": (90, 220, 255),
    "violet": (150, 120, 255),
    "mint": (72, 226, 178),
    "amber": (255, 186, 84),
    "rose": (255, 108, 150),
    "azure": (108, 156, 255),
    "mono": (232, 232, 232),
    "paper": (165, 101, 59),
    "cb_blue": (0, 114, 178),
    "cb_orange": (230, 159, 0),
    "cb_yellow": (240, 228, 66),
}
ACCENT_ORDER: tuple[str, ...] = (
    "cyan",
    "violet",
    "mint",
    "amber",
    "rose",
    "azure",
    "mono",
    "paper",
    "cb_blue",
    "cb_orange",
    "cb_yellow",
)
DEFAULT_ACCENT = "cyan"

MIN_FONT_SCALE = 0.80
MAX_FONT_SCALE = 1.50
MIN_BRIGHTNESS = 0.15
MAX_BRIGHTNESS = 1.0
POWER_SAVER_DIM = 0.85


@dataclass(frozen=True, slots=True)
class Palette:
    """Every UI colour, named by role rather than by hue."""

    bg_top: RGB
    bg_bottom: RGB
    surface: RGB
    surface_alt: RGB
    surface_raised: RGB
    stroke: RGB
    stroke_soft: RGB
    text: RGB
    text_secondary: RGB
    text_muted: RGB
    accent: RGB
    accent_ink: RGB
    accent_soft: RGB
    on_accent: RGB
    success: RGB
    warning: RGB
    danger: RGB
    scrim: RGB
    shadow: RGB
    shade: RGB
    keypad: RGB
    keypad_active: RGB
    track: RGB
    switch_off: RGB
    home_indicator: RGB


DARK_BASE = Palette(
    bg_top=(12, 16, 30),
    bg_bottom=(3, 4, 8),
    surface=(18, 22, 34),
    surface_alt=(23, 28, 43),
    surface_raised=(30, 36, 54),
    stroke=(40, 48, 68),
    stroke_soft=(28, 34, 50),
    text=(245, 248, 255),
    text_secondary=(150, 160, 182),
    text_muted=(96, 106, 128),
    accent=ACCENTS[DEFAULT_ACCENT],
    accent_ink=ACCENTS[DEFAULT_ACCENT],
    accent_soft=(28, 44, 62),
    on_accent=(8, 12, 20),
    success=(86, 222, 160),
    warning=(255, 196, 92),
    danger=(255, 104, 120),
    scrim=(0, 0, 0),
    shadow=(0, 0, 0),
    shade=(20, 24, 37),
    keypad=(26, 31, 46),
    keypad_active=(44, 52, 74),
    track=(46, 54, 74),
    switch_off=(58, 66, 88),
    home_indicator=(92, 102, 126),
)

LIGHT_BASE = Palette(
    bg_top=(247, 249, 253),
    bg_bottom=(226, 231, 241),
    surface=(255, 255, 255),
    surface_alt=(243, 246, 252),
    surface_raised=(255, 255, 255),
    stroke=(206, 214, 230),
    stroke_soft=(226, 231, 240),
    text=(18, 22, 34),
    text_secondary=(84, 94, 116),
    text_muted=(132, 142, 162),
    accent=ACCENTS[DEFAULT_ACCENT],
    accent_ink=darken(ACCENTS[DEFAULT_ACCENT], 0.35),
    accent_soft=(214, 238, 248),
    on_accent=(8, 12, 20),
    success=(24, 168, 116),
    warning=(198, 138, 18),
    danger=(216, 62, 84),
    scrim=(0, 0, 0),
    shadow=(12, 20, 36),
    shade=(252, 253, 255),
    keypad=(240, 243, 250),
    keypad_active=(222, 228, 240),
    track=(214, 221, 234),
    switch_off=(196, 204, 219),
    home_indicator=(158, 168, 186),
)


@dataclass(frozen=True, slots=True)
class SplashPalette:
    """Brand colours for the boot animation (intentionally mode independent)."""

    bg_top: RGB = (8, 11, 21)
    bg_bottom: RGB = (2, 3, 6)
    ink: RGB = (245, 248, 255)
    dim: RGB = (142, 152, 174)
    faint: RGB = (88, 98, 120)
    accent: RGB = (90, 220, 255)
    accent_2: RGB = (150, 120, 255)
    stroke: RGB = (40, 48, 68)
    logo_body: RGB = (10, 14, 24)
    logo_screen: RGB = (4, 7, 13)
    home_indicator: RGB = (92, 102, 126)


SPLASH = SplashPalette()


def palette_from_slots(slots: dict[str, tuple[int, int, int]], accent: tuple[int, int, int] | None = None) -> Palette:
    """Project the twelve-slot public theme map onto the rich UI palette."""
    bg = slots.get("bg", (10, 14, 24))
    surface = slots.get("surface", mix(bg, (255, 255, 255), 0.06))
    surface_alt = slots.get("surface_alt", mix(bg, (255, 255, 255), 0.10))
    text = slots.get("text_primary", (245, 248, 255))
    secondary = slots.get("text_secondary", mix(text, bg, 0.40))
    accent_color = accent or slots.get("accent", (90, 220, 255))
    contrast = slots.get("accent_contrast") or contrast_text(accent_color)
    border = slots.get("border", mix(bg, text, 0.20))
    dark_bg = luminance(bg) < 0.5
    return Palette(
        bg_top=mix(bg, (255, 255, 255), 0.035),
        bg_bottom=mix(bg, slots.get("shadow", (0, 0, 0)), 0.28),
        surface=surface,
        surface_alt=surface_alt,
        surface_raised=mix(surface_alt, text, 0.045),
        stroke=border,
        stroke_soft=mix(border, bg, 0.42),
        text=text,
        text_secondary=secondary,
        text_muted=mix(secondary, bg, 0.34),
        accent=accent_color,
        accent_ink=accent_color if dark_bg else darken(accent_color, 0.38),
        accent_soft=mix(surface_alt, accent_color, 0.24),
        on_accent=contrast,
        success=slots.get("success", (86, 222, 160)),
        warning=slots.get("warning", (255, 196, 92)),
        danger=slots.get("danger", (255, 104, 120)),
        scrim=(0, 0, 0),
        shadow=slots.get("shadow", (0, 0, 0)),
        shade=mix(surface_alt, bg, 0.18),
        keypad=surface_alt,
        keypad_active=mix(surface_alt, text, 0.08),
        track=mix(border, surface_alt, 0.40),
        switch_off=mix(border, bg, 0.28),
        home_indicator=mix(secondary, bg, 0.28),
    )


class Theme:
    """Appearance facade: theme packs, palettes, fonts and brightness."""

    def __init__(
        self,
        display: "Display",
        settings: "SettingsService",
        engine: ThemeEngine | None = None,
    ) -> None:
        self._display = display
        self._settings = settings
        self.engine = engine or ThemeEngine(settings)
        self._cache: Palette | None = None
        self._cache_key: tuple[object, ...] | None = None

    # -- settings-backed state ---------------------------------------------
    @property
    def mode(self) -> str:
        mode = self._settings.get("display.mode", MODE_DARK)
        return mode if mode in MODES else MODE_DARK

    @property
    def is_dark(self) -> bool:
        return self.mode == MODE_DARK

    @property
    def accent_name(self) -> str:
        name = self._settings.get("display.accent", DEFAULT_ACCENT)
        name = name if name in ACCENTS else DEFAULT_ACCENT
        if self._settings.get_bool("accessibility.colorblind_accents", False) and not name.startswith("cb_"):
            return "cb_blue"
        return name

    @property
    def accent(self) -> RGB:
        return self.palette.accent

    @property
    def font_scale(self) -> float:
        value = float(self._settings.get("display.font_scale", 1.0))
        return max(MIN_FONT_SCALE, min(MAX_FONT_SCALE, value))

    @property
    def brightness(self) -> float:
        value = float(self._settings.get("display.brightness", 0.9))
        return max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, value))

    @property
    def power_saver(self) -> bool:
        return bool(self._settings.get("display.power_saver", False))

    # -- derived appearance -------------------------------------------------
    @property
    def signature(self) -> tuple[object, ...]:
        """Key for cached surfaces.

        Brightness and power saver are deliberately excluded: they are applied
        as a fullscreen dim layer by the kernel, so they must not force a
        gradient rebuild on every slider step.
        """
        return (
            self.mode,
            self.accent_name,
            round(self.font_scale, 3),
            self._settings.get_bool("accessibility.high_contrast", False),
            self._settings.get_bool("accessibility.colorblind_accents", False),
            self.engine.resolve_key(),
            self.engine.revision,
        )

    @property
    def palette(self) -> Palette:
        key = self.signature
        if self._cache is not None and self._cache_key == key:
            return self._cache
        theme_key = self.engine.resolve_key()
        slots = self.engine.slots(theme_key)
        if self._settings.get_bool("accessibility.high_contrast", False):
            light_bg = luminance(slots["bg"]) >= 0.5
            slots["text_primary"] = (0, 0, 0) if light_bg else (255, 255, 255)
            slots["text_secondary"] = (40, 40, 40) if light_bg else (226, 232, 244)
            slots["border"] = (20, 20, 20) if light_bg else (210, 220, 240)
        accent = None
        if not theme_key.startswith("custom:"):
            accent = ACCENTS[self.accent_name]
        palette = palette_from_slots(slots, accent)
        self._cache = palette
        self._cache_key = key
        return palette

    @property
    def brightness_factor(self) -> float:
        """Multiplier for the fullscreen dim layer (1.0 = no dimming)."""
        factor = self.brightness
        if self.power_saver:
            factor *= POWER_SAVER_DIM
        return max(0.0, min(1.0, factor))

    # -- fonts ---------------------------------------------------------------
    def scaled_size(self, size: int) -> int:
        return max(8, int(round(float(size) * self.font_scale)))

    def font(self, size: int, bold: bool = False, italic: bool = False) -> pygame.font.Font:
        return self._display.font(self.scaled_size(size), bold, italic)

    def mono(self, size: int, bold: bool = False) -> pygame.font.Font:
        return self._display.mono_font(self.scaled_size(size), bold)

    # -- mutations -----------------------------------------------------------
    def set_mode(self, mode: str) -> bool:
        if mode not in MODES:
            raise ValueError(f"unknown theme mode {mode!r}")
        changed = self._settings.set("display.mode", mode)
        if self._settings.get_str("display.theme_pack", "auto") not in ("auto", "light", "dark"):
            self._settings.set("display.theme_pack", "auto")
        return changed

    def toggle_mode(self) -> str:
        mode = MODE_LIGHT if self.is_dark else MODE_DARK
        self.set_mode(mode)
        return mode

    def set_accent(self, name: str) -> bool:
        if name not in ACCENTS:
            raise ValueError(f"unknown accent {name!r}")
        return self._settings.set("display.accent", name)

    def set_font_scale(self, value: float) -> bool:
        return self._settings.set(
            "display.font_scale", max(MIN_FONT_SCALE, min(MAX_FONT_SCALE, float(value)))
        )

    def set_brightness(self, value: float) -> bool:
        return self._settings.set(
            "display.brightness", max(MIN_BRIGHTNESS, min(MAX_BRIGHTNESS, float(value)))
        )

    def set_power_saver(self, enabled: bool) -> bool:
        return self._settings.set("display.power_saver", bool(enabled))

    @staticmethod
    def accent_options() -> Iterable[tuple[str, RGB]]:
        return tuple((name, ACCENTS[name]) for name in ACCENT_ORDER)

    def __repr__(self) -> str:
        return (
            f"Theme(mode={self.mode}, accent={self.accent_name}, "
            f"font_scale={self.font_scale:.2f}, brightness={self.brightness:.2f})"
        )
