"""Settings app: a real, navigable multi-page screen with live effects.

Pages: Display, Sound, Battery, Security, About and Reset. Every control writes
straight into :class:`services.settings.SettingsService`; the theme reads those
keys live, so switching mode or accent repaints the whole OS on the next frame.

Navigation is consistent: rows slide forward, the back chevron (or Esc) slides
back, and leaving the root returns to the home screen through the kernel fade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from core.display import (
    ORIENTATION_MODES,
    REFRESH_TARGETS,
    RESOLUTION_PRESETS,
    SCALING_MODES,
    WINDOW_MODES,
)
from core.fx import WALLPAPER_SWATCHES, WALLPAPERS
from core.input import EventKind, PointerEvent
from core.scene import Scene, clamp, ease_out_cubic
from ui.layout import layout as current_layout
from ui.paint import (
    blit_text,
    draw_chevron,
    draw_rounded,
    fit_text,
    make_glow,
    vertical_gradient,
    wrap_text,
)
from ui.widgets import (
    Button,
    ChoicePicker,
    ConfirmDialog,
    Form,
    ListRow,
    Slider,
    SwatchPicker,
    TextInput,
    Toggle,
    Widget,
)

if TYPE_CHECKING:
    from ui.theme import Palette, Theme

OS_VERSION = "PhoneOS 2.0 \u00b7 Foundation"
TRANSITION_TIME = 0.26
WINDOW_MODE_LABELS = {
    "windowed": "Windowed",
    "borderless": "Borderless",
    "fullscreen": "Exclusive",
}
SCALING_LABELS = {
    "fit": "Fit (letterbox)",
    "stretch": "Stretch",
    "integer": "Integer",
    "fill": "Fill (crop)",
}
ORIENTATION_LABELS = {
    "portrait": "Portrait",
    "landscape": "Landscape",
    "auto": "Auto",
}
RESOLUTIONS = RESOLUTION_PRESETS


class Page:
    """Base sub-page: builds widgets, owns a Form, draws notes."""

    key: str = ""
    title: str = ""
    subtitle: str = ""

    def __init__(self, scene: "SettingsScene") -> None:
        self.scene = scene
        self.registry = scene.registry
        self.settings = scene.settings
        self.theme = scene.theme
        self.clock = scene.device_clock
        self.widgets: list[Widget] = []
        self.build()
        self.form = Form(self.widgets)

    @property
    def layout(self):
        return current_layout

    @property
    def body(self) -> pygame.Rect:
        return self.scene.body_rect

    def cells(
        self,
        count: int,
        *,
        preferred_height: int = 72,
        columns: int | None = None,
        gap: int = 8,
    ) -> list[pygame.Rect]:
        return self.scene.grid_rects(
            count,
            preferred_height=preferred_height,
            columns=columns,
            gap=gap,
        )

    # -- construction ---------------------------------------------------------
    def build(self) -> None:
        """Create the page's widgets."""

    def add(self, widget: Widget) -> Widget:
        self.widgets.append(widget)
        return widget

    # -- lifecycle ------------------------------------------------------------
    def refresh(self) -> None:
        """Re-read settings into the widgets (called on every page show)."""

    def update(self, dt: float) -> None:
        self.form.update(dt)

    def handle(self, event: PointerEvent) -> bool:
        return self.form.handle(event)

    def draw(self, surface: pygame.Surface, theme: "Theme") -> None:
        self.form.draw(surface, theme)

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        """Free-form readouts below the widgets."""


def _note(
    surface: pygame.Surface,
    theme: "Theme",
    rect: pygame.Rect,
    text: str,
    *,
    accent: bool = False,
) -> None:
    pal = theme.palette
    draw_rounded(
        surface,
        rect,
        fill=pal.accent_soft if accent else pal.surface_alt,
        stroke=pal.stroke_soft,
        radius=16,
    )
    font = theme.font(12)
    words = text
    lines: list[str] = []
    current = ""
    for word in words.split():
        candidate = f"{current} {word}".strip()
        if font.size(candidate)[0] <= rect.w - 28:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    y = rect.y + 14
    for line in lines[:4]:
        blit_text(surface, font, line, pal.text_secondary, topleft=(rect.x + 14, y))
        y += 18


ROOT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("display", "Display", "Window, scaling, orientation, appearance"),
    ("sound", "Sound", "Volume, mute and feedback"),
    ("appearance", "Appearance", "Theme packs, accent and contrast"),
    ("wallpaper", "Wallpaper", "Procedural scenes and motion"),
    ("home", "Home screen", "Grid, folders, icon packs and dock"),
    ("widgets", "Widgets", "Live clock, calendar, weather, music and more"),
    ("apps", "Apps", "Installed and system applications"),
    ("battery", "Battery", "Charging, drain and history"),
    ("simulation", "Simulation", "Events, calls, DND and clock speed"),
    ("connectivity", "Connectivity", "Wi-Fi, Bluetooth and airplane mode"),
    ("notifications", "Notifications", "Do Not Disturb schedule"),
    ("accessibility", "Accessibility", "Motion, contrast and touch size"),
    ("advanced", "Advanced", "Quick settings and layout tools"),
    ("security", "Security", "PIN lock and unlock behaviour"),
    ("about", "About device", "Name, version, uptime"),
    ("reset", "Reset", "Factory reset and confirmation"),
)

SEARCH_INDEX: tuple[tuple[str, str, str], ...] = tuple(
    (key, title, f"{title} {subtitle} {key}".lower()) for key, title, subtitle in ROOT_ROWS
) + (
    ("display", "Brightness", "brightness screen light dim display"),
    ("display", "Accent colour", "accent colour color theme"),
    ("display", "Dark theme", "dark light mode theme appearance"),
    ("appearance", "Theme packs", "theme pack midnight solarized monochrome"), 
    ("appearance", "Theme editor", "custom theme colour contrast hex"),
    ("wallpaper", "Wallpaper motion", "speed density seed animation wallpaper"),
    ("home", "Icon pack", "icons labels size rounded circle squircle"),
    ("home", "Home grid", "columns rows pages folder dock"),
    ("widgets", "Add widget", "clock calendar weather music battery notes widget"),
    ("apps", "Uninstall app", "install remove apps store"),
    ("connectivity", "Wi-Fi", "wifi network internet wireless online"),
    ("connectivity", "Bluetooth", "bluetooth pair device headphones"),
    ("connectivity", "Airplane mode", "airplane flight radios offline"),
    ("accessibility", "Reduce motion", "accessibility motion contrast touch font"),
    ("advanced", "Reset quick settings", "quick tiles layout reset advanced"),
    ("clipboard", "Clipboard history", "copy paste clipboard text"),
    ("backup", "Backup and restore", "backup restore snapshot data"),
    ("profiles", "Device profiles", "profile personal work data"),
    ("plugins", "Plugins", "plugin third-party app sdk loader"),
    ("dev_console", "Developer console", "logs registry debug settings"),
)


class RootPage(Page):
    key = "root"
    title = "Settings"
    subtitle = "Every setting, one search away"

    def build(self) -> None:
        self.search_rect = pygame.Rect(self.body.x, self.body.y, self.body.w, 58)
        self.search = self.add(
            TextInput(
                self.search_rect,
                "Search",
                "",
                self.scene.set_search,
                max_length=40,
                placeholder="Try 'brightness', 'theme', 'widget'",
                on_submit=self._submit_search,
            )
        )
        area = pygame.Rect(self.body.x, self.body.y + 66, self.body.w, max(80, self.body.h - 66))
        columns = 2 if current_layout.is_portrait else max(3, current_layout.columns)
        rects = current_layout.grid_rects(
            len(ROOT_ROWS),
            preferred_height=56,
            columns=columns,
            gap=7,
            body=area,
        )
        self._rows: dict[str, ListRow] = {}
        for (key, title, subtitle), rect in zip(ROOT_ROWS, rects):
            row = ListRow(
                rect,
                title,
                lambda target=key: self.scene.navigate(target),
                subtitle=subtitle,
                tone="danger" if key == "reset" else "default",
            )
            self._rows[key] = row
            self.add(row)

    def _submit_search(self, query: str) -> None:
        results = self.scene.search_matches(query)
        if results:
            self.scene.navigate(results[0][0])
            self.scene.highlight(query)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.TAP:
            for page_key, rect in self.scene.search_result_rects(self.body):
                if rect.collidepoint(event.pos):
                    query = self.scene.search_query
                    self.scene.navigate(page_key)
                    self.scene.highlight(query)
                    return True
        return super().handle(event)

    def refresh(self) -> None:
        mode = "Dark" if self.theme.is_dark else "Light"
        engine = self.scene.registry.get("theme_engine")
        pack = engine.label_for(engine.resolve_key()) if engine is not None else mode
        home = self.scene.registry.get("home_state")
        quick = self.scene.registry.get("quick_settings")
        self._rows["display"].value = f"{mode} \u00b7 {pack}"
        self._rows["sound"].value = f"{int(self.settings.get_float('sound.master_volume', 0.6) * 100)}%"
        self._rows["appearance"].value = pack
        self._rows["wallpaper"].value = self.settings.get_str("display.wallpaper", "flow").replace("_", " ").title()
        self._rows["home"].value = f"{home.grid(current_layout.is_landscape)[0]}\u00d7{home.grid(current_layout.is_landscape)[1]}" if home else "Custom"
        self._rows["widgets"].value = f"{len(home.widgets()) if home else 0} placed"
        manager = self.scene.registry.get("app_manager")
        self._rows["apps"].value = f"{len(manager.installed_ids) if manager else 0} installed"
        self._rows["battery"].value = "Saver on" if self.settings.get_bool("display.power_saver", False) else "Saver off"
        self._rows["simulation"].value = self.settings.get_str("simulation.event_rate", "normal").title()
        network = self.scene.registry.get("network")
        self._rows["connectivity"].value = network.status_label() if network is not None else "Offline"
        self._rows["notifications"].value = "DND on" if self.settings.get_bool("simulation.dnd", False) else "DND off"
        self._rows["accessibility"].value = (
            "Reduced motion" if self.settings.get_bool("accessibility.reduce_motion", False) else "Default"
        )
        self._rows["advanced"].value = f"{len(quick.tiles()) if quick else 0} quick tiles"
        self._rows["security"].value = "PIN on" if self.settings.get_bool("security.pin_enabled", True) else "PIN off"
        self._rows["about"].value = self.settings.get_str("device.name", "Nova One")
        self._rows["reset"].value = "Wipe data"

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        results = self.scene.search_matches(self.scene.search_query)
        if not self.scene.search_query:
            return
        pal = theme.palette
        if not results:
            blit_text(surface, theme.font(12), "No matching settings", pal.text_muted, topleft=(self.body.x + 8, self.body.y + 68))
            return
        for (page_key, label, _keywords), rect in zip(results[:5], self.scene.search_result_rects(self.body)):
            draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.accent, radius=16, alpha=245)
            blit_text(surface, theme.font(13, True), label, pal.text, midleft=(rect.x + 16, rect.centery))
            blit_text(surface, theme.font(10), page_key.title(), pal.accent_ink, midright=(rect.right - 16, rect.centery))


class DisplayPage(Page):
    key = "display"
    title = "Display"
    subtitle = "Window, scaling, orientation and appearance"

    def build(self) -> None:
        columns = 2 if self.layout.is_portrait else max(4, self.layout.columns)
        cells = self.cells(16, preferred_height=92, columns=columns, gap=8)
        resolution_options = tuple(
            (f"{width}x{height}", f"{width} \u00d7 {height}") for width, height in RESOLUTIONS
        ) + (("custom", "Custom"),)
        self.window_mode = self.add(
            ChoicePicker(
                cells[0],
                "Window mode",
                tuple((mode, WINDOW_MODE_LABELS[mode]) for mode in WINDOW_MODES),
                self.settings.get_str("display.window_mode", "windowed"),
                self._on_window_mode,
                subtitle="Windowed / borderless / exclusive",
            )
        )
        self.scaling = self.add(
            ChoicePicker(
                cells[1],
                "Scaling",
                tuple((mode, SCALING_LABELS[mode]) for mode in SCALING_MODES),
                self.settings.get_str("display.scaling", "fit"),
                self._on_scaling,
                subtitle="How the canvas meets the window",
            )
        )
        self.resolution = self.add(
            ChoicePicker(
                cells[2],
                "Resolution",
                resolution_options,
                "custom",
                self._on_resolution,
                subtitle="Preset or free width \u00d7 height",
            )
        )
        self.orientation = self.add(
            ChoicePicker(
                cells[3],
                "Orientation",
                tuple((mode, ORIENTATION_LABELS[mode]) for mode in ORIENTATION_MODES),
                self.settings.get_str("display.orientation", "portrait"),
                self._on_orientation,
                subtitle="Auto follows the window aspect",
            )
        )
        self.refresh_rate = self.add(
            ChoicePicker(
                cells[4],
                "Refresh target",
                tuple((str(value), f"{value} Hz") for value in REFRESH_TARGETS),
                str(self.settings.get_int("display.refresh_rate", 60)),
                self._on_refresh,
                subtitle="Kernel pacing / F1 shows actual",
            )
        )
        self.vsync = self.add(
            Toggle(
                cells[5],
                "V-Sync",
                self.settings.get_bool("display.vsync", False),
                self._on_vsync,
                subtitle="Falls back safely when unsupported",
            )
        )
        self.device_frame = self.add(
            Toggle(
                cells[6],
                "Show device frame",
                self.settings.get_bool("display.device_frame", False),
                self._on_device_frame,
                subtitle="Procedural bezel around the canvas",
            )
        )
        current_w, current_h = self.scene.display.windowed_size
        self.width_input = self.add(
            TextInput(
                cells[7],
                "Width (px)",
                str(current_w),
                max_length=4,
                allowed=str.isdigit,
                placeholder=str(RESOLUTIONS[0][0]),
            )
        )
        self.height_input = self.add(
            TextInput(
                cells[8],
                "Height (px)",
                str(current_h),
                max_length=4,
                allowed=str.isdigit,
                placeholder=str(RESOLUTIONS[0][1]),
            )
        )
        self.apply_button = self.add(
            Button(
                cells[9],
                "Apply resolution",
                self.scene.apply_resolution_from_fields,
                subtitle="320\u00d7480 \u2013 2160\u00d74800",
            )
        )
        self.brightness = self.add(
            Slider(
                cells[10],
                "Brightness",
                self.settings.get_float("display.brightness", 0.9),
                0.15,
                1.0,
                self._on_brightness,
                step=0.01,
                formatter=lambda value: f"{value * 100:.0f}%",
            )
        )
        self.wallpaper = self.add(
            SwatchPicker(
                cells[11],
                "Wallpaper",
                tuple((name, WALLPAPER_SWATCHES[name]) for name in WALLPAPERS),
                self.settings.get_str("display.wallpaper", "flow"),
                self._on_wallpaper,
            )
        )
        self.accent = self.add(
            SwatchPicker(
                cells[12],
                "Accent",
                self.theme.accent_options(),
                self.settings.get_str("display.accent", "cyan"),
                self._on_accent,
            )
        )
        self.mode = self.add(
            Toggle(
                cells[13],
                "Dark theme",
                self.theme.is_dark,
                self._on_mode,
                subtitle="Live re-theme",
            )
        )
        self.font_scale = self.add(
            Slider(
                cells[14],
                "Font scale",
                self.settings.get_float("display.font_scale", 1.0),
                0.80,
                1.50,
                self._on_font_scale,
                step=0.05,
                formatter=lambda value: f"{value:.2f}\u00d7",
            )
        )
        self.crt = self.add(
            Toggle(
                cells[15],
                "CRT scanlines",
                self.settings.get_bool("display.fx_crt", False),
                self._on_crt,
                subtitle="numpy pass \u00b7 off by default",
            )
        )

    # -- handlers ------------------------------------------------------------
    def _on_window_mode(self, mode: str) -> None:
        self.settings.set("display.window_mode", mode)

    def _on_scaling(self, mode: str) -> None:
        self.settings.set("display.scaling", mode)

    def _on_resolution(self, value: str) -> None:
        if value == "custom":
            return
        try:
            width, height = (int(part) for part in value.split("x", 1))
        except ValueError:
            return
        self.scene.apply_resolution(width, height)

    def _on_orientation(self, mode: str) -> None:
        self.settings.set("display.orientation", mode)
        if mode == "auto" or self.scene.display.window_mode != "windowed":
            return
        width, height = self.scene.display.windowed_size
        target = (max(width, height), min(width, height)) if mode == "landscape" else (min(width, height), max(width, height))
        if target != (width, height):
            self.scene.display.set_window_size(target)
            final_w, final_h = self.scene.display.windowed_size
            self.settings.set("display.window_width", final_w)
            self.settings.set("display.window_height", final_h)

    def _on_refresh(self, value: str) -> None:
        try:
            self.settings.set("display.refresh_rate", int(value))
        except ValueError:
            return

    def _on_vsync(self, enabled: bool) -> None:
        self.settings.set("display.vsync", enabled)

    def _on_device_frame(self, enabled: bool) -> None:
        self.settings.set("display.device_frame", enabled)

    def _on_brightness(self, value: float) -> None:
        self.settings.set("display.brightness", round(value, 3))

    def _on_accent(self, name: str) -> None:
        self.settings.set("display.accent", name)

    def _on_wallpaper(self, name: str) -> None:
        self.settings.set("display.wallpaper", name)

    def _on_mode(self, dark: bool) -> None:
        self.theme.set_mode("dark" if dark else "light")

    def _on_font_scale(self, value: float) -> None:
        self.settings.set("display.font_scale", round(value, 2))

    def _on_crt(self, enabled: bool) -> None:
        self.settings.set("display.fx_crt", enabled)

    def refresh(self) -> None:
        self.window_mode.select(self.settings.get_str("display.window_mode", "windowed"))
        self.scaling.select(self.settings.get_str("display.scaling", "fit"))
        self.orientation.select(self.settings.get_str("display.orientation", "portrait"))
        refresh = self.settings.get_int("display.refresh_rate", 60)
        self.refresh_rate.select(str(refresh))
        self.vsync.set_value(self.settings.get_bool("display.vsync", False))
        self.device_frame.set_value(self.settings.get_bool("display.device_frame", False))
        width, height = self.scene.display.windowed_size
        match = f"{width}x{height}"
        self.resolution.select(match if any(pair == (width, height) for pair in RESOLUTIONS) else "custom")
        self.width_input.set_value(str(width))
        self.height_input.set_value(str(height))
        self.brightness.set_value(self.settings.get_float("display.brightness", 0.9))
        self.accent.select(self.settings.get_str("display.accent", "cyan"))
        self.wallpaper.select(self.settings.get_str("display.wallpaper", "flow"))
        self.mode.set_value(self.theme.is_dark)
        self.font_scale.set_value(self.settings.get_float("display.font_scale", 1.0))
        self.crt.set_value(self.settings.get_bool("display.fx_crt", False))


class SoundPage(Page):
    key = "sound"
    title = "Sound"
    subtitle = "Simulated audio controls"

    def build(self) -> None:
        cells = self.cells(2, preferred_height=88)
        self.volume = self.add(
            Slider(
                cells[0],
                "Master volume",
                self.settings.get_float("sound.master_volume", 0.6),
                0.0,
                1.0,
                self._on_volume,
                step=0.05,
                formatter=lambda value: f"{value * 100:.0f}%",
                subtitle="Music, video and UI cues",
            )
        )
        self.muted = self.add(
            Toggle(
                cells[1],
                "Mute",
                self.settings.get_bool("sound.muted", False),
                self._on_mute,
                subtitle="Silences media and UI cues; alarms still ring",
            )
        )

    def _on_volume(self, value: float) -> None:
        self.settings.set("sound.master_volume", round(value, 3))

    def _on_mute(self, value: bool) -> None:
        self.settings.set("sound.muted", bool(value))

    def refresh(self) -> None:
        self.muted.set_value(self.settings.get_bool("sound.muted", False))
        self.volume.set_value(self.settings.get_float("sound.master_volume", 0.6))

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        pal = theme.palette
        card = pygame.Rect(self.body.x, self.body.y + 200, self.body.w, min(190, self.body.bottom - self.body.y - 200))
        draw_rounded(surface, card, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=16)
        blit_text(
            surface,
            theme.font(14, True),
            "PROCEDURAL AUDIO \u00b7 NO ASSET FILES",
            pal.accent_ink,
            topleft=(card.x + 16, card.y + 16),
            tracking=1.4,
        )
        body = (
            "Music, video ambience and UI cues are synthesised with numpy at runtime. "
            "Mute silences media and UI sounds; alarm and call rings still break "
            "through. If the mixer cannot initialise, media apps stay visual-only."
        )
        font = theme.font(12)
        y = card.y + 48
        for line in wrap_text(font, body, card.w - 32):
            blit_text(surface, font, line, pal.text_secondary, topleft=(card.x + 16, y))
            y += 19


class BatteryPage(Page):
    key = "battery"
    title = "Battery"
    subtitle = "Charging, app-aware drain and rolling history"

    def build(self) -> None:
        cells = self.cells(2, preferred_height=72, columns=2)
        self.saver = self.add(
            Toggle(
                cells[0],
                "Power saver",
                self.settings.get_bool("display.power_saver", False),
                self._on_saver,
                subtitle="Halves drain \u00b7 dims UI",
            )
        )
        self.plug = self.add(
            Button(
                cells[1],
                "Plug in",
                self._on_plug,
                tone="ghost",
                subtitle="Simulated charger",
            )
        )

    @property
    def _power(self):
        return self.scene.registry.require("power")

    def _on_saver(self, enabled: bool) -> None:
        self.settings.set("display.power_saver", enabled)

    def _on_plug(self) -> None:
        self._power.toggle_charging()
        self.refresh()

    def refresh(self) -> None:
        self.saver.set_value(self.settings.get_bool("display.power_saver", False))
        self.plug.label = "Unplug" if self._power.charging else "Plug in"
        self.plug.tone = "danger" if self._power.charging else "ghost"

    def update(self, dt: float) -> None:
        super().update(dt)
        self.plug.label = "Unplug" if self._power.charging else "Plug in"
        self.plug.tone = "danger" if self._power.charging else "ghost"

    def _content_rects(self) -> tuple[pygame.Rect, pygame.Rect]:
        top = max(self.saver.rect.bottom, self.plug.rect.bottom) + 10
        available = pygame.Rect(self.body.x, top, self.body.w, self.body.bottom - top)
        if current_layout.is_landscape:
            half = (available.w - 10) // 2
            return (
                pygame.Rect(available.x, available.y, half, available.h),
                pygame.Rect(available.x + half + 10, available.y, half, available.h),
            )
        summary_h = min(250, max(150, int(available.h * 0.48)))
        return (
            pygame.Rect(available.x, available.y, available.w, summary_h),
            pygame.Rect(available.x, available.y + summary_h + 10, available.w, available.h - summary_h - 10),
        )

    def _summary(self, surface, theme, rect: pygame.Rect) -> None:
        pal = theme.palette
        power = self._power
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        percent = power.percent
        per_hour = power.percent_per_hour
        if self.clock.charging:
            remaining = "charging"
        elif per_hour <= 0.001:
            remaining = "--"
        else:
            hours = percent / per_hour
            remaining = f"{int(hours)}h {int((hours - int(hours)) * 60):02d}m"
        rows = (
            ("Battery level", f"{percent:.1f}%"),
            ("Power draw", f"{power.power_draw_ma:.0f} mA"),
            ("Rate", f"{per_hour:.2f} %/h"),
            ("Charging", "YES" if power.charging else "NO"),
            ("Estimated life", remaining),
        )
        y = rect.y + 14
        for label, value in rows:
            blit_text(surface, theme.font(11), label, pal.text_secondary, topleft=(rect.x + 16, y))
            blit_text(
                surface,
                theme.mono(11, True),
                value,
                pal.text,
                midright=(rect.right - 16, y + 7),
            )
            y += 24
        track = pygame.Rect(rect.x + 16, rect.bottom - 22, rect.w - 32, 7)
        pygame.draw.rect(surface, pal.track, track, border_radius=4)
        fill_width = max(2, int(track.w * clamp(percent / 100.0, 0.0, 1.0)))
        pygame.draw.rect(
            surface,
            pal.success if percent > 20 else pal.danger,
            pygame.Rect(track.x, track.y, fill_width, track.h),
            border_radius=4,
        )

    def _graph(self, surface, theme, rect: pygame.Rect) -> None:
        pal = theme.palette
        draw_rounded(surface, rect, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        blit_text(
            surface,
            theme.font(11, True),
            "BATTERY HISTORY",
            pal.text_muted,
            topleft=(rect.x + 16, rect.y + 12),
            tracking=1.2,
        )
        chart = pygame.Rect(rect.x + 16, rect.y + 34, rect.w - 32, rect.h - 52)
        if chart.h < 20:
            return
        for step in range(1, 4):
            y = chart.y + chart.h * step / 4
            pygame.draw.line(surface, pal.stroke_soft, (chart.x, y), (chart.right, y), 1)
        points = self._power.history_points()
        if len(points) < 2:
            blit_text(
                surface,
                theme.font(11),
                "Collecting samples\u2026",
                pal.text_muted,
                center=chart.center,
            )
            return
        values = [percent for _x, percent in points]
        low = max(0.0, min(values) - 0.25)
        high = min(100.0, max(values) + 0.25)
        span = max(0.5, high - low)
        mapped = [
            (
                chart.x + int(x * chart.w),
                chart.bottom - int(clamp((percent - low) / span, 0.0, 1.0) * chart.h),
            )
            for x, percent in points
        ]
        pygame.draw.lines(surface, pal.accent, False, mapped, 2)
        pygame.draw.circle(surface, pal.accent, mapped[-1], 4)
        blit_text(
            surface,
            theme.mono(9),
            f"{self._power.percent:.0f}%",
            pal.accent_ink,
            midright=(chart.right, chart.y),
        )

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        summary, graph = self._content_rects()
        self._summary(surface, theme, summary)
        self._graph(surface, theme, graph)


class SecurityPage(Page):
    key = "security"
    title = "Security"
    subtitle = "PIN unlock and fallback behaviour"

    def build(self) -> None:
        cells = self.cells(2, preferred_height=86)
        self.require_pin = self.add(
            Toggle(
                cells[0],
                "Require PIN",
                self.settings.get_bool("security.pin_enabled", True),
                self._on_require_pin,
                subtitle="Off: swipe up unlocks the device",
            )
        )
        self.pin = self.add(
            TextInput(
                cells[1],
                "Device PIN (4 digits)",
                self.settings.get_str("security.pin", "0000"),
                self._on_pin,
                max_length=4,
                placeholder="0000",
                allowed=str.isdigit,
            )
        )

    def _on_require_pin(self, enabled: bool) -> None:
        self.settings.set("security.pin_enabled", enabled)

    def _on_pin(self, text: str) -> None:
        digits = "".join(character for character in text if character.isdigit())[:4]
        if len(digits) == 4:
            self.settings.set("security.pin", digits)

    def refresh(self) -> None:
        self.require_pin.set_value(self.settings.get_bool("security.pin_enabled", True))
        self.pin.set_value(self.settings.get_str("security.pin", "0000"))

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        _note(
            surface,
            theme,
            pygame.Rect(self.body.x, self.body.y + 200, self.body.w, min(96, self.body.bottom - self.body.y - 200)),
            "The PIN is stored in data/settings.json. After three failed attempts the "
            "keypad cools down (10s, then 20s, up to 30s). Factory reset restores 0000.",
            accent=True,
        )


class AboutPage(Page):
    key = "about"
    title = "About device"
    subtitle = "Identity, build and runtime information"

    def build(self) -> None:
        specs = ("version", "model", "display", "uptime", "notifications")
        cells = self.cells(1 + len(specs), preferred_height=76)
        self.name_input = self.add(
            TextInput(
                cells[0],
                "Device name",
                self.settings.get_str("device.name", "Nova One"),
                self._on_name,
                max_length=24,
                placeholder="Nova One",
            )
        )
        self._rows: dict[str, ListRow] = {}
        for key, rect in zip(specs, cells[1:]):
            row = ListRow(rect, key.title(), None, chevron=False)
            row.focusable = False
            self._rows[key] = row
            self.add(row)

    def _on_name(self, text: str) -> None:
        trimmed = " ".join(text.split())[:24]
        if trimmed:
            self.settings.set("device.name", trimmed)

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.TAP and self._rows["version"].rect.collidepoint(event.pos):
            self._version_taps = getattr(self, "_version_taps", 0) + 1
            if self._version_taps >= 7:
                self._version_taps = 0
                self.scene.navigate("dev_console")
            else:
                remaining = 7 - self._version_taps
                self.scene.notifications.post("settings", "Developer options", f"{remaining} more taps", self.theme.palette.accent, banner=False)
            return True
        return super().handle(event)

    def refresh(self) -> None:
        self.name_input.set_value(self.settings.get_str("device.name", "Nova One"))
        self._rows["version"].title = "OS version"
        self._rows["version"].value = OS_VERSION
        self._rows["model"].title = "Model"
        self._rows["model"].value = "Virtual device"
        self._rows["display"].title = "Display"
        width, height = self.scene.display.virtual_size
        orientation = self.scene.display.orientation.title()
        self._rows["display"].value = f"{width} \u00d7 {height} \u00b7 {orientation}"
        self._rows["uptime"].title = "Uptime"
        self._rows["uptime"].value = self.clock.uptime_string()
        self._rows["notifications"].title = "Notifications"
        unread = self.scene.notifications.unread_count
        total = self.scene.notifications.count
        self._rows["notifications"].value = f"{unread} unread \u00b7 {total} total"

    def update(self, dt: float) -> None:
        super().update(dt)
        # Uptime ticks while the page is open.
        self._rows["uptime"].value = self.clock.uptime_string()

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        return


class SimulationPage(Page):
    key = "simulation"
    title = "Simulation"
    subtitle = "Ambient events, calls, DND and time"

    def build(self) -> None:
        columns = 2 if self.layout.is_portrait else 4
        cells = self.cells(11, preferred_height=78, columns=columns)
        hours = tuple((str(hour), f"{hour:02d}:00") for hour in range(24))
        self.event_rate = self.add(
            ChoicePicker(
                cells[0],
                "Event rate",
                (("off", "Off"), ("low", "Low"), ("normal", "Normal"), ("high", "High")),
                self.settings.get_str("simulation.event_rate", "normal"),
                lambda value: self.settings.set("simulation.event_rate", value),
            )
        )
        self.calls = self.add(
            Slider(
                cells[1],
                "Calls per hour",
                self.settings.get_float("simulation.calls_per_hour", 0.25),
                0.0,
                6.0,
                lambda value: self.settings.set("simulation.calls_per_hour", round(value, 2)),
                step=0.05,
                formatter=lambda value: f"{value:.2f}/h",
            )
        )
        self.notification_rate = self.add(
            Slider(
                cells[2],
                "Notifications / h",
                float(self.settings.get_int("simulation.notification_rate", 6)),
                0.0,
                30.0,
                lambda value: self.settings.set("simulation.notification_rate", int(round(value))),
                step=1.0,
                formatter=lambda value: f"{int(value)}/h",
            )
        )
        self.drain = self.add(
            Slider(
                cells[3],
                "Battery drain",
                self.settings.get_float("simulation.drain_multiplier", 1.0),
                0.25,
                4.0,
                lambda value: self.settings.set("simulation.drain_multiplier", round(value, 2)),
                step=0.25,
                formatter=lambda value: f"{value:.2f}\u00d7",
            )
        )
        self.clock_speed = self.add(
            ChoicePicker(
                cells[4],
                "Clock speed",
                (("1", "1\u00d7"), ("10", "10\u00d7"), ("60", "60\u00d7")),
                str(self.settings.get_int("simulation.clock_speed", 1)),
                lambda value: self.settings.set("simulation.clock_speed", int(value)),
            )
        )
        self.dnd = self.add(
            Toggle(
                cells[5],
                "Do Not Disturb",
                self.settings.get_bool("simulation.dnd", False),
                lambda value: self.settings.set("simulation.dnd", value),
                subtitle="Silences ambient events and calls",
            )
        )
        self.dnd_start = self.add(
            ChoicePicker(
                cells[6],
                "DND start",
                hours,
                str(self.settings.get_int("simulation.dnd_start", 22)),
                lambda value: self.settings.set("simulation.dnd_start", int(value)),
            )
        )
        self.dnd_end = self.add(
            ChoicePicker(
                cells[7],
                "DND end",
                hours,
                str(self.settings.get_int("simulation.dnd_end", 7)),
                lambda value: self.settings.set("simulation.dnd_end", int(value)),
            )
        )
        self.auto_reboot = self.add(
            Toggle(
                cells[8],
                "Auto-reboot",
                self.settings.get_bool("simulation.auto_reboot", False),
                lambda value: self.settings.set("simulation.auto_reboot", value),
                subtitle="Boot again after shutdown",
            )
        )
        self.trigger_call = self.add(
            Button(
                cells[9],
                "Test incoming call",
                self.scene.trigger_incoming_call,
                tone="ghost",
            )
        )
        self.trigger_event = self.add(
            Button(
                cells[10],
                "Emit ambient event",
                self.scene.trigger_ambient_event,
                tone="ghost",
            )
        )

    def refresh(self) -> None:
        self.event_rate.select(self.settings.get_str("simulation.event_rate", "normal"))
        self.calls.set_value(self.settings.get_float("simulation.calls_per_hour", 0.25))
        self.notification_rate.set_value(float(self.settings.get_int("simulation.notification_rate", 6)))
        self.drain.set_value(self.settings.get_float("simulation.drain_multiplier", 1.0))
        self.clock_speed.select(str(self.settings.get_int("simulation.clock_speed", 1)))
        self.dnd.set_value(self.settings.get_bool("simulation.dnd", False))
        self.dnd_start.select(str(self.settings.get_int("simulation.dnd_start", 22)))
        self.dnd_end.select(str(self.settings.get_int("simulation.dnd_end", 7)))
        self.auto_reboot.set_value(self.settings.get_bool("simulation.auto_reboot", False))


class NotificationsPage(Page):
    key = "notifications"
    title = "Notifications"
    subtitle = "Do Not Disturb schedule"

    def build(self) -> None:
        cells = self.cells(3, preferred_height=80, columns=1 if self.layout.is_portrait else 3)
        hours = tuple((str(hour), f"{hour:02d}:00") for hour in range(24))
        self.dnd = self.add(
            Toggle(
                cells[0],
                "Do Not Disturb",
                self.settings.get_bool("simulation.dnd", False),
                lambda value: self.settings.set("simulation.dnd", value),
                subtitle="Ambient notifications and calls stay silent",
            )
        )
        self.start = self.add(
            ChoicePicker(
                cells[1],
                "Start",
                hours,
                str(self.settings.get_int("simulation.dnd_start", 22)),
                lambda value: self.settings.set("simulation.dnd_start", int(value)),
            )
        )
        self.end = self.add(
            ChoicePicker(
                cells[2],
                "End",
                hours,
                str(self.settings.get_int("simulation.dnd_end", 7)),
                lambda value: self.settings.set("simulation.dnd_end", int(value)),
            )
        )

    def refresh(self) -> None:
        self.dnd.set_value(self.settings.get_bool("simulation.dnd", False))
        self.start.select(str(self.settings.get_int("simulation.dnd_start", 22)))
        self.end.select(str(self.settings.get_int("simulation.dnd_end", 7)))


class ResetPage(Page):
    key = "reset"
    title = "Reset"
    subtitle = "Destructive actions require confirmation"

    def build(self) -> None:
        self.reset_button = self.add(
            Button(
                self.cells(1, preferred_height=74)[0],
                "Factory reset",
                self.scene.open_reset_dialog,
                tone="danger",
                subtitle="Wipes settings and app data",
            )
        )

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        pal = theme.palette
        card = pygame.Rect(self.body.x, self.body.y + 96, self.body.w, min(188, self.body.bottom - self.body.y - 96))
        draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        blit_text(
            surface,
            theme.font(14, True),
            "What gets reset",
            pal.text,
            topleft=(card.x + 18, card.y + 16),
        )
        bullets = (
            "Window mode, scaling, resolution, orientation and refresh",
            "Brightness, accent, wallpaper, theme mode and font scale",
            "Simulation rates, DND schedule and clock speed",
            "Power saver, charging state and simulated volume",
            "Device name, DND state and the 4-digit PIN",
            "Notification history",
        )
        y = card.y + 48
        for bullet in bullets:
            pygame.draw.circle(surface, pal.danger, (card.x + 22, y + 8), 3)
            blit_text(surface, theme.font(13), bullet, pal.text_secondary, topleft=(card.x + 34, y))
            y += 26

        if current_layout.compact:
            return
        _note(
            surface,
            theme,
            pygame.Rect(self.body.x, card.bottom + 12, self.body.w, 72),
            "The confirmation dialog defaults to Cancel. Enter confirms only after you "
            "move focus with Tab or the arrow keys.",
            accent=True,
        )


class AppearancePage(Page):
    key = "appearance"
    title = "Appearance"
    subtitle = "Theme packs, colour slots and contrast guard"

    def build(self) -> None:
        engine = self.scene.registry.require("theme_engine")
        options = [("auto", "Automatic (light/dark)")]
        options += [(key, label) for key, label, _slots in engine.all_themes()]
        rects = self.cells(3, preferred_height=62, columns=2, gap=8)
        self.pack = self.add(
            ChoicePicker(
                rects[0],
                "Theme pack",
                tuple(options),
                self.settings.get_str("display.theme_pack", "auto"),
                self._on_pack,
                subtitle="Six built-in families plus your saved themes",
            )
        )
        self.edit = self.add(Button(rects[1], "Custom theme editor", lambda: self.scene.navigate("theme_editor"), subtitle="12 colour slots"))
        self.fix = self.add(Button(rects[2], "Fix contrast", self._fix_contrast, tone="primary", subtitle="Ensure 4.5:1 text contrast"))

    def _on_pack(self, key: str) -> None:
        self.scene.registry.require("theme_engine").apply(key)
        self.scene.refresh_all()

    def _fix_contrast(self) -> None:
        engine = self.scene.registry.require("theme_engine")
        slots = engine.auto_fix(engine.slots())
        key = engine.save_custom("High contrast", slots)
        if key is not None:
            engine.apply(key)
            self.scene.refresh_all()
            self.notifications.post("settings", "Contrast fixed", "Saved as the High contrast theme.", self.theme.palette.success)

    @property
    def notifications(self):
        return self.scene.notifications

    def refresh(self) -> None:
        engine = self.scene.registry.require("theme_engine")
        key = self.settings.get_str("display.theme_pack", "auto")
        self.pack.select(key)
        self._contrast = engine.contrast(engine.slots())

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        pal = theme.palette
        card = pygame.Rect(self.body.x, self.body.y + 150, self.body.w, 188)
        draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=20)
        blit_text(surface, theme.font(13, True), "Live preview", pal.text, topleft=(card.x + 18, card.y + 14))
        engine = self.scene.registry.require("theme_engine")
        slots = engine.slots()
        swatch = pygame.Rect(card.x + 18, card.y + 44, card.w - 36, 54)
        draw_rounded(surface, swatch, fill=slots["bg"], radius=14)
        for index, slot in enumerate(("surface", "accent", "text_primary", "success", "warning", "danger")):
            width = swatch.w // 6
            color_rect = pygame.Rect(swatch.x + index * width, swatch.y, width, swatch.h)
            pygame.draw.rect(surface, slots[slot], color_rect)
        contrast = engine.contrast(slots)
        good = contrast >= 4.5
        blit_text(
            surface,
            theme.font(12, True),
            f"Text contrast {contrast:.2f}:1 \u00b7 {'AA pass' if good else 'needs fixing'}",
            pal.success if good else pal.warning,
            topleft=(card.x + 18, card.y + 112),
        )
        blit_text(
            surface,
            theme.font(11),
            "Built-in packs keep their own accent. Tap Fix contrast to save a corrected copy.",
            pal.text_secondary,
            topleft=(card.x + 18, card.y + 140),
        )


class ThemeEditorPage(Page):
    key = "theme_editor"
    title = "Theme editor"
    subtitle = "Edit twelve slots, preview instantly, save as a custom pack"

    def build(self) -> None:
        engine = self.scene.registry.require("theme_engine")
        slots = engine.slots()
        from services.theme_engine import SLOTS, SLOT_LABELS, rgb_to_hex

        columns = 2 if current_layout.is_portrait else 4
        rects = self.cells(14, preferred_height=58, columns=columns, gap=7)
        self.name_input = self.add(TextInput(rects[0], "Theme name", "My theme", max_length=24, placeholder="Custom theme"))
        self.slot_inputs: dict[str, TextInput] = {}
        for index, slot in enumerate(SLOTS):
            field = self.add(
                TextInput(
                    rects[index + 1],
                    SLOT_LABELS[slot],
                    rgb_to_hex(slots[slot]),
                    self._refresh_preview,
                    max_length=7,
                    allowed=lambda character: character in "#0123456789abcdefABCDEF",
                    placeholder="#rrggbb",
                )
            )
            self.slot_inputs[slot] = field
        self.save = self.add(Button(rects[13], "Save & apply theme", self._save, subtitle="Stored locally in data/themes.json"))
        self._preview_slots = slots
        self._swatch_rects: list[tuple[str, pygame.Rect]] = []

    def _read_slots(self) -> dict[str, tuple[int, int, int]] | None:
        from services.theme_engine import SLOTS, hex_to_rgb

        result = {}
        for slot in SLOTS:
            color = hex_to_rgb(self.slot_inputs[slot].value)
            if color is None:
                return None
            result[slot] = color
        return result

    def _refresh_preview(self, _value: str = "") -> None:
        slots = self._read_slots()
        if slots is not None:
            self._preview_slots = slots

    def _save(self) -> None:
        engine = self.scene.registry.require("theme_engine")
        slots = self._read_slots()
        if slots is None:
            self.notifications.post("settings", "Invalid colour", "Use #rrggbb hex for every slot.", self.theme.palette.danger)
            return
        key = engine.save_custom(self.name_input.value, slots)
        if key is None:
            self.notifications.post("settings", "Name required", "Give the theme a short name.", self.theme.palette.warning)
            return
        engine.apply(key)
        self.scene.refresh_all()
        self.notifications.post("settings", "Theme saved", f"{self.name_input.value} is now active.", self.theme.palette.success)

    @property
    def notifications(self):
        return self.scene.notifications

    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.TAP:
            for value, rect in self._swatch_rects:
                if rect.collidepoint(event.pos):
                    focused = self.form.focused_widget
                    target = next((slot for slot, field in self.slot_inputs.items() if field is focused), "accent")
                    self.slot_inputs[target].set_value(value, notify=True)
                    return True
        return super().handle(event)

    def refresh(self) -> None:
        self._refresh_preview()

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        pal = theme.palette
        offset = 430 if current_layout.is_portrait else 250
        card = pygame.Rect(self.body.x, self.body.y + offset, self.body.w, min(190, self.body.bottom - self.body.y - offset))
        if card.h < 60:
            return
        draw_rounded(surface, card, fill=self._preview_slots["bg"], stroke=self._preview_slots["border"], radius=20)
        blit_text(surface, theme.font(20, True), self.name_input.value or "Custom theme", self._preview_slots["text_primary"], topleft=(card.x + 20, card.y + 18))
        blit_text(surface, theme.font(12), "Preview uses your current colour slots.", self._preview_slots["text_secondary"], topleft=(card.x + 20, card.y + 48))
        button = pygame.Rect(card.x + 20, card.y + 76, 132, 38)
        draw_rounded(surface, button, fill=self._preview_slots["accent"], radius=12)
        blit_text(surface, theme.font(12, True), "Accent button", self._preview_slots["accent_contrast"], center=button.center)

        # 6x6 swatch grid: tap a swatch to write it into the focused slot.
        from services.theme_engine import ThemeEngine, rgb_to_hex

        self._swatch_rects = []
        grid_x = card.right - 132
        grid_y = card.y + 18
        cell = 16
        gap = 4
        for row in range(6):
            for col in range(6):
                hue = col / 6.0
                saturation = 1.0 - row * 0.15
                value = 0.45 + row * 0.10
                color = ThemeEngine.hsv_color(hue, saturation, value)
                swatch = pygame.Rect(grid_x + col * (cell + gap), grid_y + row * (cell + gap), cell, cell)
                draw_rounded(surface, swatch, fill=color, radius=4)
                self._swatch_rects.append((rgb_to_hex(color), swatch))
        blit_text(surface, theme.font(8, True), "6×6 SWATCHES", self._preview_slots["text_secondary"], topright=(card.right - 18, card.y + 142))


class WallpaperPage(Page):
    key = "wallpaper"
    title = "Wallpaper"
    subtitle = "Eleven procedural scenes, motion and power-saver rules"

    def build(self) -> None:
        from core.fx import WALLPAPER_CATEGORIES, WALLPAPER_LABELS

        options = []
        for category, names in WALLPAPER_CATEGORIES.items():
            options.extend((name, f"{category} \u00b7 {WALLPAPER_LABELS[name]}") for name in names)
        rects = self.cells(9, preferred_height=62, columns=2, gap=8)
        self.picker = self.add(
            ChoicePicker(rects[0], "Wallpaper", tuple(options), self.settings.get_str("display.wallpaper", "flow"), self._on_pick, subtitle="Grouped by scene family")
        )
        self.random = self.add(Button(rects[1], "Randomise now", self._on_random, subtitle="New wallpaper + new seed"))
        self.speed = self.add(
            Slider(rects[2], "Motion speed", self.settings.get_float("display.wallpaper_speed", 1.0), 0.1, 3.0, self._on_speed, step=0.05, formatter=lambda value: f"{value:.2f}\u00d7")
        )
        self.density = self.add(
            Slider(rects[3], "Detail density", self.settings.get_float("display.wallpaper_density", 1.0), 0.1, 2.0, self._on_density, step=0.05, formatter=lambda value: f"{value:.2f}\u00d7")
        )
        self.palette = self.add(
            ChoicePicker(rects[4], "Palette", (("theme", "Theme accent"), ("custom", "Custom two-colour")), self.settings.get_str("display.wallpaper_palette", "theme"), self._on_palette, subtitle="Follow theme or pick your own")
        )
        self.color1 = self.add(TextInput(rects[5], "Colour 1", self.settings.get_str("display.wallpaper_color1", "#5adcff"), self._on_color1, max_length=7, placeholder="#5adcff"))
        self.color2 = self.add(TextInput(rects[6], "Colour 2", self.settings.get_str("display.wallpaper_color2", "#7f78ff"), self._on_color2, max_length=7, placeholder="#7f78ff"))
        self.unlock = self.add(
            Toggle(rects[7], "Random on unlock", self.settings.get_bool("display.wallpaper_random_unlock", False), self._on_unlock, subtitle="Rotate once per unlock")
        )
        self.saver = self.add(
            Toggle(rects[8], "Power saver freezes wallpaper", self.settings.get_bool("display.wallpaper_power_saver_disable", True), self._on_saver, subtitle="Replace animation with a static gradient")
        )

    def _on_pick(self, value: str) -> None:
        self.settings.set("display.wallpaper", value)

    def _on_random(self) -> None:
        fx = self.registry.get("fx")
        if fx is not None:
            fx.randomize()
        self.refresh()

    def _on_speed(self, value: float) -> None:
        self.settings.set("display.wallpaper_speed", round(value, 3))

    def _on_density(self, value: float) -> None:
        self.settings.set("display.wallpaper_density", round(value, 3))

    def _on_palette(self, value: str) -> None:
        self.settings.set("display.wallpaper_palette", value)

    def _on_color1(self, value: str) -> None:
        if len(value) in (4, 7):
            self.settings.set("display.wallpaper_color1", value)

    def _on_color2(self, value: str) -> None:
        if len(value) in (4, 7):
            self.settings.set("display.wallpaper_color2", value)

    def _on_unlock(self, enabled: bool) -> None:
        self.settings.set("display.wallpaper_random_unlock", enabled)

    def _on_saver(self, enabled: bool) -> None:
        self.settings.set("display.wallpaper_power_saver_disable", enabled)

    def refresh(self) -> None:
        self.picker.select(self.settings.get_str("display.wallpaper", "flow"))
        self.speed.set_value(self.settings.get_float("display.wallpaper_speed", 1.0))
        self.density.set_value(self.settings.get_float("display.wallpaper_density", 1.0))
        self.palette.select(self.settings.get_str("display.wallpaper_palette", "theme"))
        self.color1.set_value(self.settings.get_str("display.wallpaper_color1", "#5adcff"))
        self.color2.set_value(self.settings.get_str("display.wallpaper_color2", "#7f78ff"))
        self.unlock.set_value(self.settings.get_bool("display.wallpaper_random_unlock", False))
        self.saver.set_value(self.settings.get_bool("display.wallpaper_power_saver_disable", True))


class HomePage(Page):
    key = "home"
    title = "Home screen"
    subtitle = "Grid, pages, icon pack, dock and editor shortcuts"

    def build(self) -> None:
        self.home = self.scene.registry.require("home_state")
        from ui.icon_packs import PACKS

        cols, rows = self.home.grid(current_layout.is_landscape)
        rects = self.cells(9, preferred_height=60, columns=2, gap=8)
        self.columns = self.add(
            ChoicePicker(rects[0], "Grid columns", tuple((str(value), f"{value} columns") for value in range(2, 7)), str(cols), self._on_columns)
        )
        self.rows = self.add(
            ChoicePicker(rects[1], "Grid rows", tuple((str(value), f"{value} rows") for value in range(2, 7)), str(rows), self._on_rows)
        )
        self.labels = self.add(Toggle(rects[2], "Show icon labels", self.home.icons.get("labels", True), self._on_labels))
        self.pack = self.add(
            ChoicePicker(rects[3], "Icon pack", tuple((pack.key, pack.label) for pack in PACKS), self.home.icons.get("pack", "rounded"), self._on_pack)
        )
        self.scale = self.add(
            Slider(rects[4], "Icon size", self.home.icons.get("scale", 1.0), 0.7, 1.3, self._on_scale, step=0.05, formatter=lambda value: f"{value:.2f}\u00d7")
        )
        self.dock_show = self.add(Toggle(rects[5], "Show dock", self.home.dock.get("show", True), self._on_dock_show))
        self.dock_max = self.add(
            ChoicePicker(rects[6], "Dock slots", tuple((str(value), f"{value} icons") for value in range(2, 7)), str(self.home.dock.get("max", 4)), self._on_dock_max)
        )
        self.reset = self.add(Button(rects[7], "Reset home layout", self._reset, tone="danger", subtitle="Pages, folders and widgets return to defaults"))
        self.quick_edit = self.add(Button(rects[8], "Long-press empty space", lambda: None, subtitle="Also opens this page from the launcher"))

    def _on_columns(self, value: str) -> None:
        _cols, rows = self.home.grid(current_layout.is_landscape)
        self.home.set_grid(current_layout.is_landscape, int(value), rows)
        self.scene.rebuild_home()

    def _on_rows(self, value: str) -> None:
        cols, _rows = self.home.grid(current_layout.is_landscape)
        self.home.set_grid(current_layout.is_landscape, cols, int(value))
        self.scene.rebuild_home()

    def _on_labels(self, enabled: bool) -> None:
        self.home.set_icon_option("labels", enabled)

    def _on_pack(self, key: str) -> None:
        self.home.set_icon_option("pack", key)

    def _on_scale(self, value: float) -> None:
        self.home.set_icon_option("scale", round(value, 2))

    def _on_dock_show(self, enabled: bool) -> None:
        self.home.set_dock_option("show", enabled)

    def _on_dock_max(self, value: str) -> None:
        self.home.set_dock_option("max", int(value))

    def _reset(self) -> None:
        self.home.reset()
        manager = self.registry.get("app_manager")
        if manager is not None:
            manager.bind_home_state(self.home)
        self.scene.rebuild_home()
        self.notifications.post("settings", "Home reset", "Launcher layout restored.", self.theme.palette.success)

    @property
    def notifications(self):
        return self.scene.notifications

    def refresh(self) -> None:
        cols, rows = self.home.grid(current_layout.is_landscape)
        self.columns.select(str(cols))
        self.rows.select(str(rows))
        self.labels.set_value(self.home.icons.get("labels", True))
        self.pack.select(self.home.icons.get("pack", "rounded"))
        self.scale.set_value(self.home.icons.get("scale", 1.0))
        self.dock_show.set_value(self.home.dock.get("show", True))
        self.dock_max.select(str(self.home.dock.get("max", 4)))


class WidgetsPage(Page):
    key = "widgets"
    title = "Widgets"
    subtitle = "Add live widgets; long-press on Home to move, resize or remove"

    def build(self) -> None:
        self.home = self.scene.registry.require("home_state")
        from widgets import WIDGET_LABELS, WIDGETS

        rects = self.cells(7, preferred_height=60, columns=2, gap=8)
        self.add_buttons = {}
        for index, (kind, label) in enumerate(WIDGET_LABELS.items()):
            button = self.add(Button(rects[index], f"Add {label}", lambda target=kind: self._add(target), subtitle=f"Default size {self.home.WIDGET_SIZES.get(kind, (2, 1))[0]}\u00d7{self.home.WIDGET_SIZES.get(kind, (2, 1))[1]}" if hasattr(self.home, "WIDGET_SIZES") else "Live data"))
            self.add_buttons[kind] = button
        self.clock_24h = self.add(Toggle(rects[6], "Clock widgets use 24-hour time", True, self._on_24h, subtitle="Applies to every clock widget"))

    def _add(self, kind: str) -> None:
        widgets = self.home.widgets(0)
        from services.home_state import WIDGET_SIZES

        width, height = WIDGET_SIZES.get(kind, (2, 1))
        engine = self.scene.registry.get("home_state")
        cols, rows = self.home.grid(current_layout.is_landscape)
        from ui.widgets_engine import WidgetEngine

        slot = WidgetEngine().find_free_rect(widgets, cols, rows, width, height)
        if slot is None:
            self.notifications.post("settings", "No room", "Remove or shrink another widget first.", self.theme.palette.warning)
            return
        self.home.add_widget(kind, page=0, col=slot[0], row=slot[1], width=width, height=height)
        self.notifications.post("settings", "Widget added", f"{kind.title()} placed on page 1.", self.theme.palette.success)

    def _on_24h(self, enabled: bool) -> None:
        for widget in self.home.widgets():
            self.home.update_widget(str(widget.get("id", "")), options={"24h": enabled})

    @property
    def notifications(self):
        return self.scene.notifications

    def refresh(self) -> None:
        self._count = len(self.home.widgets())
        self.clock_24h.set_value(True)

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        _note(
            surface,
            theme,
            pygame.Rect(self.body.x, self.body.y + 320, self.body.w, 72),
            f"{getattr(self, '_count', 0)} widgets placed. Long-press any widget on Home for resize and remove controls.",
            accent=True,
        )


class AppsPage(Page):
    key = "apps"
    title = "Apps"
    subtitle = "Launch or inspect installed applications"

    def build(self) -> None:
        manager = self.scene.registry.require("app_manager")
        manifests = list(manager.catalog())
        rects = self.cells(len(manifests), preferred_height=56, columns=2 if current_layout.is_portrait else 3, gap=7)
        self._rows: list[tuple[str, ListRow]] = []
        for manifest, rect in zip(manifests, rects):
            row = ListRow(
                rect,
                manifest.name,
                lambda app_id=manifest.app_id: self._open(app_id),
                subtitle=manifest.category,
                value="Open",
            )
            self._rows.append((manifest.app_id, row))
            self.add(row)

    def _open(self, app_id: str) -> None:
        manager = self.scene.registry.require("app_manager")
        if manager.launch(app_id) is not None:
            self.scene.go("app", fade=0.16)

    def refresh(self) -> None:
        manager = self.scene.registry.require("app_manager")
        for app_id, row in self._rows:
            row.value = "Installed" if manager.is_installed(app_id) else "System"
            row.subtitle = (manager.manifest(app_id).category if manager.manifest(app_id) else "")


class ConnectivityPage(Page):
    key = "connectivity"
    title = "Connectivity"
    subtitle = "Simulated radios, Wi-Fi networks and Bluetooth devices"

    def build(self) -> None:
        from services.network import BLUETOOTH_DEVICES, WIFI_NETWORKS

        self.network = self.scene.registry.require("network")
        rects = self.cells(5, preferred_height=64, columns=2, gap=8)
        self.wifi = self.add(Toggle(rects[0], "Wi-Fi", self.settings.get_bool("quick.wifi", True), self._on_wifi, subtitle="Required for Browser and online apps"))
        self.bluetooth = self.add(Toggle(rects[1], "Bluetooth", self.settings.get_bool("quick.bluetooth", False), self._on_bluetooth, subtitle="Simulated paired accessories"))
        self.airplane = self.add(Toggle(rects[2], "Airplane mode", self.settings.get_bool("quick.airplane", False), self._on_airplane, subtitle="Turns off Wi-Fi and Bluetooth"))
        self.wifi_net = self.add(
            ChoicePicker(rects[3], "Wi-Fi network", tuple((name, name) for name in WIFI_NETWORKS), self.settings.get_str("quick.wifi_network", WIFI_NETWORKS[0]), self._on_wifi_net, subtitle="Pick a simulated access point")
        )
        self.bt_device = self.add(
            ChoicePicker(rects[4], "Bluetooth device", tuple((name, name) for name in ("",) + BLUETOOTH_DEVICES), self.settings.get_str("quick.bluetooth_device", ""), self._on_bt_device, subtitle="Pair or disconnect")
        )

    def _on_wifi(self, enabled: bool) -> None:
        self.network.set_wifi(enabled)

    def _on_bluetooth(self, enabled: bool) -> None:
        self.network.set_bluetooth(enabled)

    def _on_airplane(self, enabled: bool) -> None:
        self.network.set_airplane(enabled)
        self.refresh()

    def _on_wifi_net(self, value: str) -> None:
        self.network.connect_wifi(value)

    def _on_bt_device(self, value: str) -> None:
        if value:
            self.network.pair_bluetooth(value)
        else:
            self.network.disconnect_bluetooth()

    def refresh(self) -> None:
        self.wifi.set_value(self.settings.get_bool("quick.wifi", True))
        self.bluetooth.set_value(self.settings.get_bool("quick.bluetooth", False))
        self.airplane.set_value(self.settings.get_bool("quick.airplane", False))
        self.wifi_net.select(self.settings.get_str("quick.wifi_network", "HomeNet"))
        self.bt_device.select(self.settings.get_str("quick.bluetooth_device", ""))

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        _note(
            surface,
            theme,
            pygame.Rect(self.body.x, self.body.y + 220, self.body.w, 64),
            f"Internet: {'online' if self.network.internet else 'offline'} · Wi-Fi {self.network.status_label()} · Bluetooth {self.network.bluetooth_device or 'not paired'}",
            accent=self.network.internet,
        )


class AccessibilityPage(Page):
    key = "accessibility"
    title = "Accessibility"
    subtitle = "Motion, contrast, touch targets and type size"

    def build(self) -> None:
        rects = self.cells(5, preferred_height=62, columns=2, gap=8)
        self.reduce = self.add(
            Toggle(rects[0], "Reduce motion", self.settings.get_bool("accessibility.reduce_motion", False), self._on_reduce, subtitle="Disables blur, parallax and long transitions")
        )
        self.contrast = self.add(
            Toggle(rects[1], "High contrast text", self.settings.get_bool("accessibility.high_contrast", False), self._on_contrast, subtitle="Boosts primary text contrast")
        )
        self.touch = self.add(
            Toggle(rects[2], "Larger touch targets", self.settings.get_bool("accessibility.large_touch", False), self._on_touch, subtitle="Adds breathing room to controls")
        )
        self.colorblind = self.add(
            Toggle(rects[3], "Color-blind safe accents", self.settings.get_bool("accessibility.colorblind_accents", False), self._on_colorblind, subtitle="Blue/orange palette for accent roles")
        )
        self.font = self.add(
            Slider(rects[4], "Font scale", self.settings.get_float("display.font_scale", 1.0), 0.80, 1.50, self._on_font, step=0.05, formatter=lambda value: f"{value:.2f}\u00d7")
        )

    def _on_reduce(self, enabled: bool) -> None:
        self.settings.set("accessibility.reduce_motion", enabled)

    def _on_contrast(self, enabled: bool) -> None:
        self.settings.set("accessibility.high_contrast", enabled)

    def _on_touch(self, enabled: bool) -> None:
        self.settings.set("accessibility.large_touch", enabled)

    def _on_colorblind(self, enabled: bool) -> None:
        self.settings.set("accessibility.colorblind_accents", enabled)
        self.theme._cache_key = None

    def _on_font(self, value: float) -> None:
        self.theme.set_font_scale(value)

    def refresh(self) -> None:
        self.reduce.set_value(self.settings.get_bool("accessibility.reduce_motion", False))
        self.contrast.set_value(self.settings.get_bool("accessibility.high_contrast", False))
        self.touch.set_value(self.settings.get_bool("accessibility.large_touch", False))
        self.colorblind.set_value(self.settings.get_bool("accessibility.colorblind_accents", False))
        self.font.set_value(self.settings.get_float("display.font_scale", 1.0))


class BackupPage(Page):
    key = "backup"
    title = "Backup & restore"
    subtitle = "Chunked copies of ./data into ./backups"

    def build(self) -> None:
        self.backup = self.scene.registry.require("backup")
        rects = self.cells(6, preferred_height=60, columns=2, gap=8)
        self.create = self.add(Button(rects[0], "Backup now", self._backup, subtitle="Copies data/ with a frame budget"))
        self.restore = self.add(Button(rects[1], "Restore latest", self._restore_latest, tone="danger", subtitle="Snapshots current data first"))
        self._rows: list[ListRow] = []
        self._row_area = pygame.Rect(self.body.x, self.body.y + 140, self.body.w, max(80, self.body.h - 140))

    def _backup(self) -> None:
        self.backup.begin_backup()
        self.notifications.post("settings", "Backup started", "Copying data in the background.", self.theme.palette.accent, banner=False)

    def _restore_latest(self) -> None:
        rows = self.backup.list_backups()
        if not rows:
            self.notifications.post("settings", "No backups", "Create a backup first.", self.theme.palette.warning)
            return
        latest = rows[0].name
        self._restore(latest)

    def _restore(self, name: str) -> None:
        self.scene.open_confirm("Restore backup?", f"Restore {name}? Current data is snapshotted first.", lambda: self.backup.begin_restore(name))

    def refresh(self) -> None:
        for row in self._rows:
            if row in self.widgets:
                self.widgets.remove(row)
        self._rows = []
        rows = self.backup.list_backups()[:5]
        rects = current_layout.grid_rects(len(rows), preferred_height=52, columns=1, gap=6, body=self._row_area)
        for backup, rect in zip(rows, rects):
            row = ListRow(rect, backup.name.replace("backup_", ""), lambda name=backup.name: self._restore(name), subtitle=str(sum(1 for p in backup.rglob("*") if p.is_file())) + " files", value="Restore")
            self._rows.append(row)
            self.widgets.append(row)
        self.form.widgets = list(self.widgets)

    @property
    def notifications(self):
        return self.scene.notifications


class ProfilesPage(Page):
    key = "profiles"
    title = "Device profiles"
    subtitle = "Personal and Work keep independent data folders"

    def build(self) -> None:
        self.profiles = self.scene.registry.require("profiles")
        rects = self.cells(2, preferred_height=78, columns=1, gap=10)
        self._rows: dict[str, ListRow] = {}
        for name, label, rect in zip(("personal", "work"), ("Personal", "Work"), rects):
            row = ListRow(rect, label, lambda target=name: self._switch(target), subtitle=f"data/profiles/{name}")
            self._rows[name] = row
            self.add(row)

    def _switch(self, name: str) -> None:
        if name == self.profiles.active:
            return
        self.scene.open_confirm("Switch profile?", f"Switch to {name.title()}? Current data is saved to the active profile.", lambda: self._apply(name))

    def _apply(self, name: str) -> None:
        self.settings.flush()
        self.profiles.switch(name, self.scene.kernel.reload_data)
        self.notifications.post("settings", "Profile switched", f"{name.title()} data is active.", self.theme.palette.success)

    def refresh(self) -> None:
        for name, row in self._rows.items():
            row.value = "Active" if name == self.profiles.active else "Switch"

    @property
    def notifications(self):
        return self.scene.notifications


class PluginsPage(Page):
    key = "plugins"
    title = "Plugins"
    subtitle = "Third-party apps loaded from ./plugins"

    def build(self) -> None:
        self.plugins = self.scene.registry.require("plugins")
        self.reload_button = self.add(Button(self.cells(1, preferred_height=52)[0], "Reload plugins", self._reload, subtitle="Rescans the plugins directory"))
        self._rows: list[tuple[str, ListRow]] = []
        self._area = pygame.Rect(self.body.x, self.body.y + 70, self.body.w, max(80, self.body.h - 80))

    def _reload(self) -> None:
        self.plugins.reload()
        self.refresh()
        self.notifications.post("settings", "Plugins reloaded", self.plugins.status(), self.theme.palette.accent)

    def _toggle(self, app_id: str, enabled: bool) -> None:
        self.plugins.set_enabled(app_id, not enabled)
        self.refresh()

    def refresh(self) -> None:
        for _app_id, row in self._rows:
            if row in self.widgets:
                self.widgets.remove(row)
        self._rows = []
        infos = self.plugins.plugins[:8]
        rects = current_layout.grid_rects(max(1, len(infos)), preferred_height=54, columns=1, gap=6, body=self._area)
        for info, rect in zip(infos, rects):
            row = ListRow(rect, info.name, None, subtitle=info.error or info.path.rsplit("/", 1)[-1], value="Enabled" if info.enabled else "Disabled", tone="danger" if info.error else "default")
            row.on_press = lambda target=info.app_id, state=info.enabled: self._toggle(target, state)
            self._rows.append((info.app_id, row))
            self.widgets.append(row)
        self.form.widgets = list(self.widgets)

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        _note(surface, theme, pygame.Rect(self.body.x, self.body.bottom - 54, self.body.w, 46), self.plugins.last_error or self.plugins.status(), accent=not bool(self.plugins.last_error))

    @property
    def notifications(self):
        return self.scene.notifications


class DevConsolePage(Page):
    key = "dev_console"
    title = "Developer console"
    subtitle = "Live logs, settings, app registry and tools"

    def build(self) -> None:
        rects = self.cells(4, preferred_height=52, columns=2, gap=8)
        self.logs = self.add(Button(rects[0], "Clear logs", self._clear_logs, subtitle="Ring buffer only"))
        self.reload = self.add(Button(rects[1], "Reload plugins", self._reload, subtitle="Rescan ./plugins"))
        self.selftest = self.add(Button(rects[2], "Run self-test", lambda: self.scene.kernel.toggle_qa(), subtitle="Opens F2 self-test"))
        self.clear_data = self.add(Button(rects[3], "Clear data", self._confirm_clear, tone="danger", subtitle="Wipes active profile data"))

    def _clear_logs(self) -> None:
        from services import logger

        logger.clear()

    def _reload(self) -> None:
        self.registry.require("plugins").reload()

    def _confirm_clear(self) -> None:
        self.scene.open_confirm("Clear all data?", "Deletes settings and user data for the active profile. Back up first if needed.", self._clear_data)

    def _clear_data(self) -> None:
        import shutil

        storage = self.registry.require("storage")
        root = storage.root
        for child in list(root.iterdir()):
            if child.name == "profiles":
                continue
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            except OSError:
                pass
        self.scene.kernel.reload_data()

    def draw_notes(self, surface: pygame.Surface, theme: "Theme") -> None:
        from services import logger

        pal = theme.palette
        area = pygame.Rect(self.body.x, self.body.y + 122, self.body.w, max(80, self.body.h - 122))
        draw_rounded(surface, area, fill=pal.surface, stroke=pal.stroke_soft, radius=18)
        x = area.x + 14
        y = area.y + 12
        mono = theme.mono(9)
        blit_text(surface, theme.font(10, True), "SERVICE LOG", pal.accent_ink, topleft=(x, y), tracking=1)
        y += 20
        for entry in logger.recent(12, "INFO"):
            line = f"{entry.time_label()} {entry.level:<7} {entry.message}"
            blit_text(surface, mono, fit_text(mono, line, area.w - 28), pal.text_secondary, topleft=(x, y))
            y += 12
            if y > area.bottom - 120:
                break
        y += 8
        blit_text(surface, theme.font(10, True), "APP REGISTRY", pal.accent_ink, topleft=(x, y), tracking=1)
        y += 18
        manager = self.registry.get("app_manager")
        if manager is not None:
            rows = manager.catalog()[:7]
            blit_text(surface, mono, " id                 name              installed", pal.text_muted, topleft=(x, y))
            y += 12
            for manifest in rows:
                blit_text(surface, mono, f" {manifest.app_id:<18} {manifest.name[:17]:<17} {'yes' if manager.is_installed(manifest.app_id) else 'no'}", pal.text_secondary, topleft=(x, y))
                y += 12
        y += 6
        blit_text(surface, theme.font(10, True), "SETTINGS SNAPSHOT", pal.accent_ink, topleft=(x, y), tracking=1)
        y += 16
        import json

        snapshot = json.dumps(self.settings._data, sort_keys=True)
        for line in [snapshot[i : i + 58] for i in range(0, min(len(snapshot), 174), 58)]:
            blit_text(surface, mono, line, pal.text_muted, topleft=(x, y))
            y += 11


class ClipboardPage(Page):
    key = "clipboard"
    title = "Clipboard"
    subtitle = "Last ten copied strings; tap one to make it current"

    def build(self) -> None:
        self.clipboard = self.scene.registry.require("clipboard")
        self._rows: list[ListRow] = []
        self._area = pygame.Rect(self.body.x, self.body.y + 56, self.body.w, max(80, self.body.h - 56))

    def _use(self, text: str) -> None:
        self.clipboard.use(text)
        self.refresh()

    def _clear(self) -> None:
        self.clipboard.clear()
        self.refresh()

    def refresh(self) -> None:
        for row in self._rows:
            if row in self.widgets:
                self.widgets.remove(row)
        if getattr(self, "_clear_button", None) in self.widgets:
            self.widgets.remove(self._clear_button)
        self._rows = []
        values = list(self.clipboard.history)[:9]
        self._clear_button = None
        if values:
            self._clear_button = Button(pygame.Rect(self.body.x, self.body.y, 150, 42), "Clear history", self._clear, tone="danger")
            self.widgets.append(self._clear_button)
        rects = current_layout.grid_rects(len(values), preferred_height=54, columns=1, gap=6, body=self._area)
        for text, rect in zip(values, rects):
            preview = " ".join(text.split())[:64]
            row = ListRow(rect, preview, lambda value=text: self._use(value), subtitle=f"{len(text)} chars", value="Use")
            self._rows.append(row)
            self.widgets.append(row)
        self.form.widgets = list(self.widgets)


class AdvancedPage(Page):
    key = "advanced"
    title = "Advanced"
    subtitle = "Layout tools, backup, profiles, plugins and maintenance"

    def build(self) -> None:
        rects = self.cells(10, preferred_height=54, columns=2, gap=8)
        self.backup_row = self.add(Button(rects[0], "Backup & restore", lambda: self.scene.navigate("backup"), subtitle="Chunked data copies"))
        self.profiles_row = self.add(Button(rects[1], "Device profiles", lambda: self.scene.navigate("profiles"), subtitle="Personal / Work"))
        self.plugins_row = self.add(Button(rects[2], "Plugins", lambda: self.scene.navigate("plugins"), subtitle="Load third-party apps"))
        self.dev_row = self.add(Button(rects[3], "Developer console", lambda: self.scene.navigate("dev_console"), subtitle="Logs, settings, registry"))
        self.quick_reset = self.add(Button(rects[4], "Reset quick settings", self._reset_quick, subtitle="Restore default tiles"))
        self.home_reset = self.add(Button(rects[5], "Reset home layout", self._reset_home, tone="danger", subtitle="Pages, folders and widgets"))
        self.themes_reset = self.add(Button(rects[6], "Delete custom themes", self._reset_themes, subtitle="Keeps built-in packs"))
        self.notify = self.add(Button(rects[7], "Clear notifications", self._clear, subtitle="Removes shade history"))
        self.clipboard_row = self.add(Button(rects[8], "Clipboard history", lambda: self.scene.navigate("clipboard"), subtitle="Last 10 copied strings"))
        self.clear_clipboard = self.add(Button(rects[9], "Clear clipboard history", self._clear_clipboard, subtitle="Erase without opening"))

    def _clear_clipboard(self) -> None:
        clipboard = self.registry.get("clipboard")
        if clipboard is not None:
            clipboard.clear()
        self.notifications.post("settings", "Clipboard cleared", "History is empty.", self.theme.palette.success)

    def _reset_quick(self) -> None:
        quick = self.scene.registry.get("quick_settings")
        if quick is not None:
            quick.reset()
        self.notifications.post("settings", "Quick settings reset", "Default tiles restored.", self.theme.palette.success)

    def _reset_home(self) -> None:
        home = self.scene.registry.get("home_state")
        if home is not None:
            home.reset()
            manager = self.registry.get("app_manager")
            if manager is not None:
                manager.bind_home_state(home)
        self.scene.rebuild_home()
        self.notifications.post("settings", "Home reset", "Launcher restored.", self.theme.palette.success)

    def _reset_themes(self) -> None:
        engine = self.scene.registry.get("theme_engine")
        if engine is not None:
            for key, _label, _slots in list(engine.customs()):
                engine.delete_custom(key)
        self.scene.refresh_all()
        self.notifications.post("settings", "Themes deleted", "Custom packs removed.", self.theme.palette.warning)

    def _clear(self) -> None:
        self.scene.notifications.clear()
        self.notifications.post("settings", "Notifications cleared", "The shade is empty.", self.theme.palette.success)

    @property
    def notifications(self):
        return self.scene.notifications


PAGE_CLASSES: tuple[type[Page], ...] = (
    RootPage,
    DisplayPage,
    SoundPage,
    BatteryPage,
    SimulationPage,
    NotificationsPage,
    ConnectivityPage,
    SecurityPage,
    AboutPage,
    ResetPage,
    AppearancePage,
    ThemeEditorPage,
    WallpaperPage,
    HomePage,
    WidgetsPage,
    AppsPage,
    AccessibilityPage,
    AdvancedPage,
    ClipboardPage,
    BackupPage,
    ProfilesPage,
    PluginsPage,
    DevConsolePage,
)


class SettingsScene(Scene):
    """Settings root + sub-pages with a shared slide transition."""

    name = "settings"

    def __init__(self, kernel) -> None:
        super().__init__(kernel)
        self._layer = pygame.Surface(self.size, pygame.SRCALPHA)
        self._chrome_key: tuple[object, ...] | None = None
        self._background: pygame.Surface | None = None
        self._glow: pygame.Surface | None = None

        self._pages: dict[str, Page] = {}
        self._current = "root"
        self._outgoing: str | None = None
        self._transition = 1.0
        self._direction = 1
        self._dialog: ConfirmDialog | None = None
        self.search_query = ""
        self._highlight_text = ""
        self._highlight_t = 0.0
        self._build_pages()

    # -- layout ---------------------------------------------------------------
    @property
    def body_rect(self) -> pygame.Rect:
        back = self._back_rect()
        top = back.bottom + (12 if current_layout.compact else 16)
        bottom = current_layout.height - current_layout.bottom - 24
        return pygame.Rect(
            current_layout.margin,
            top,
            current_layout.width - current_layout.margin * 2,
            max(80, bottom - top),
        )

    def grid_rects(
        self,
        count: int,
        *,
        preferred_height: int = 72,
        columns: int | None = None,
        gap: int = 8,
    ) -> list[pygame.Rect]:
        return current_layout.grid_rects(
            count,
            preferred_height=preferred_height,
            columns=columns,
            gap=gap,
            body=self.body_rect,
        )

    # -- setup ----------------------------------------------------------------
    def _build_pages(self) -> None:
        self._pages = {cls.key: cls(self) for cls in PAGE_CLASSES}

    # -- search ---------------------------------------------------------------
    def set_search(self, query: str) -> None:
        self.search_query = str(query)

    def search_matches(self, query: str) -> list[tuple[str, str, str]]:
        text = " ".join(str(query).lower().split())
        if not text:
            return []
        words = text.split()
        scored = []
        seen: set[tuple[str, str]] = set()
        for page_key, label, keywords in SEARCH_INDEX:
            haystack = keywords.lower()
            if not all(word in haystack for word in words):
                continue
            identity = (page_key, label)
            if identity in seen:
                continue
            seen.add(identity)
            score = 0
            if label.lower().startswith(words[0]):
                score += 4
            if label.lower() == text:
                score += 8
            scored.append((score, page_key, label, keywords))
        scored.sort(key=lambda item: (-item[0], item[2]))
        return [(page_key, label, keywords) for _score, page_key, label, keywords in scored]

    def search_result_rects(self, body: pygame.Rect) -> list[pygame.Rect]:
        results = self.search_matches(self.search_query)
        return [pygame.Rect(body.x + 4, body.y + 66 + index * 44, body.w - 8, 40) for index in range(min(5, len(results)))]

    def highlight(self, query: str) -> None:
        self._highlight_text = " ".join(str(query).split())
        self._highlight_t = 2.4

    def refresh_all(self) -> None:
        home = self.registry.get("home_state")
        if home is not None:
            self.theme.engine.revision += 1
        for page in self._pages.values():
            page.refresh()

    def rebuild_home(self) -> None:
        # HomeScene reads the model when it is next constructed; touching the
        # theme revision also refreshes any cached palette previews.
        self.theme.engine.revision += 1

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self._layer = pygame.Surface(new, pygame.SRCALPHA)
        self._chrome_key = None
        self._background = None
        self._glow = None
        self._dialog = None
        current = self._current
        self._build_pages()
        self._current = current if current in self._pages else "root"
        self._outgoing = None
        self._transition = 1.0
        self._pages[self._current].refresh()
        self._pages[self._current].form.focus_first()

    # -- resolution -----------------------------------------------------------
    def apply_resolution_from_fields(self) -> None:
        page = self._pages.get("display")
        if not isinstance(page, DisplayPage):
            return
        try:
            width = int(page.width_input.value)
            height = int(page.height_input.value)
        except ValueError:
            width, height = self.display.windowed_size
        self.apply_resolution(width, height)

    def apply_resolution(self, width: int, height: int) -> None:
        self.settings.set("display.window_mode", "windowed")
        self.display.set_window_size((width, height))
        final_w, final_h = self.display.windowed_size
        self.settings.set("display.window_width", final_w)
        self.settings.set("display.window_height", final_h)
        page = self._pages.get("display")
        if isinstance(page, DisplayPage):
            page.refresh()

    def trigger_incoming_call(self) -> None:
        telephony = self.registry.get("telephony")
        if telephony is not None:
            telephony.start_incoming(force=True)

    def trigger_ambient_event(self) -> None:
        sim = self.registry.get("sim_events")
        if sim is not None:
            sim.force_event()

    def on_enter(self) -> None:
        super().on_enter()
        self.input.cancel()
        self.input.ui_capture_keys = True
        pending = getattr(self.kernel, "_pending_settings_page", None)
        self.kernel._pending_settings_page = None
        self._current = pending if pending in self._pages else "root"
        self._outgoing = None
        self._transition = 1.0
        for page in self._pages.values():
            page.refresh()
        self._pages[self._current].form.focus_first()
        highlight = getattr(self.kernel, "_pending_settings_highlight", None)
        self.kernel._pending_settings_highlight = None
        if highlight:
            self.set_search(str(highlight))
            self.highlight(str(highlight))

    def on_exit(self) -> None:
        super().on_exit()
        self.input.ui_capture_keys = False
        self._dialog = None
        self.settings.flush()

    # -- navigation -----------------------------------------------------------
    def navigate(self, key: str, *, forward: bool = True) -> None:
        if key not in self._pages or key == self._current:
            return
        self._outgoing = self._current
        self._current = key
        self._transition = 0.0
        self._direction = 1 if forward else -1
        page = self._pages[key]
        page.refresh()
        page.form.focus_first()

    def go_back(self) -> None:
        if self._current != "root":
            self.navigate("root", forward=False)
            return
        self.go("home", fade=0.22)

    def open_confirm(self, title: str, body: str, callback) -> None:
        self._dialog = ConfirmDialog(
            pygame.Rect(0, 0, *self.size),
            title,
            body,
            confirm_label="Confirm",
            cancel_label="Cancel",
            danger=True,
            on_confirm=callback,
        )
        self._dialog.open()

    def open_reset_dialog(self) -> None:
        if self._dialog is None:
            self._dialog = ConfirmDialog(
                pygame.Rect(0, 0, *self.size),
                "Factory reset?",
                "Every preference is removed and data/settings.json is rewritten with "
                "defaults. The PIN goes back to 0000 and notifications are cleared.",
                confirm_label="Reset",
                cancel_label="Cancel",
                danger=True,
                on_confirm=self._perform_reset,
            )
        self._dialog.open()

    def _perform_reset(self) -> None:
        self.settings.reset()
        self.notifications.clear()
        self._build_pages()
        self._current = "root"
        self._outgoing = None
        self._transition = 1.0
        self._pages["root"].refresh()
        self._pages["root"].form.focus_first()
        self.notifications.post(
            "system",
            "Factory reset complete",
            "All preferences are back to their defaults.",
            self.theme.palette.success,
        )

    # -- frame ----------------------------------------------------------------
    def update(self, dt: float) -> None:
        super().update(dt)
        if self._highlight_t > 0.0:
            self._highlight_t = max(0.0, self._highlight_t - dt)
        if self._transition < 1.0:
            self._transition = min(1.0, self._transition + dt / TRANSITION_TIME)
            if self._transition >= 1.0:
                self._outgoing = None

        page = self._pages.get(self._current)
        if page is not None:
            page.update(dt)

        if self._dialog is not None:
            self._dialog.update(dt)

    def handle(self, event: PointerEvent) -> bool:
        if self._dialog is not None and self._dialog.is_open:
            return self._dialog.handle(event)

        if self._transition < 1.0:
            return True

        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self.go_back()
            return True

        if event.kind is EventKind.TAP and self._back_rect().collidepoint(event.pos):
            self.go_back()
            return True

        page = self._pages.get(self._current)
        if page is None:
            return False
        return page.handle(event)

    @staticmethod
    def _back_rect() -> pygame.Rect:
        y = current_layout.status_bar_height + (16 if current_layout.compact else 30)
        return pygame.Rect(current_layout.margin, y, 44, 44)

    # -- render ---------------------------------------------------------------
    def _ensure_chrome(self) -> "Palette":
        palette = self.theme.palette
        key = self.theme.signature
        if self._chrome_key != key or self._background is None or self._glow is None:
            self._chrome_key = key
            self._background = vertical_gradient(self.size, palette.bg_top, palette.bg_bottom)
            glow = make_glow(220, palette.accent, power=2.4)
            side = max(360, min(self.size[0], 780))
            self._glow = pygame.transform.smoothscale(glow, (side, side))
        return palette

    def draw(self, surface: pygame.Surface) -> None:
        palette = self._ensure_chrome()
        assert self._background is not None and self._glow is not None

        surface.blit(self._background, (0, 0))
        self._glow.set_alpha(34)
        header_y = current_layout.status_bar_height + (20 if current_layout.compact else 52)
        surface.blit(self._glow, self._glow.get_rect(center=(current_layout.center_x, header_y)))

        self._draw_header(surface, palette)

        page = self._pages.get(self._current)
        if page is None:
            return

        if self._transition < 1.0 and self._outgoing is not None:
            eased = ease_out_cubic(self._transition)
            outgoing = self._pages.get(self._outgoing)
            if outgoing is not None:
                self._draw_layer(surface, outgoing, -self._direction * 64.0 * eased, int(255 * (1.0 - eased)))
            self._draw_layer(surface, page, self._direction * 64.0 * (1.0 - eased), 255)
        else:
            self._draw_layer(surface, page, 0.0, 255)

        # Page navigation also gets the numpy displacement pass, so in-app
        # swipes and kernel screen changes share the same language.
        if self._transition < 1.0 and not (self._dialog is not None and self._dialog.is_open):
            fx = self.registry.get("fx")
            if fx is not None:
                fx.displace(surface, (1.0 - self._transition) * 0.35, phase=self.elapsed)

        self._draw_hint(surface, palette)
        if self._highlight_t > 0.0 and self._highlight_text:
            chip = pygame.Rect(current_layout.margin, current_layout.status_bar_height + 6, min(self.size[0] - 48, 300), 26)
            draw_rounded(surface, chip, fill=palette.accent, radius=13, alpha=220)
            blit_text(surface, self.theme.font(10, True), f"Search: {self._highlight_text}", palette.on_accent, center=chip.center)

        if self._dialog is not None and self._dialog.is_open:
            self._dialog.draw(surface, self.theme)

        backup = self.registry.get("backup")
        job = getattr(backup, "job", None) if backup is not None else None
        if job is not None and not job.done:
            pal = self.theme.palette
            scrim = pygame.Surface(self.size, pygame.SRCALPHA)
            scrim.fill((*pal.scrim, 150))
            surface.blit(scrim, (0, 0))
            card = pygame.Rect(0, 0, min(self.size[0] - 60, 420), 170)
            card.center = (current_layout.center_x, current_layout.height // 2)
            draw_rounded(surface, card, fill=pal.surface, stroke=pal.stroke_soft, radius=24, alpha=250)
            blit_text(surface, self.theme.font(18, True), job.kind.title(), pal.text, center=(card.centerx, card.y + 34))
            blit_text(surface, self.theme.font(11), job.label, pal.text_secondary, center=(card.centerx, card.y + 60))
            track = pygame.Rect(card.x + 28, card.y + 88, card.w - 56, 8)
            draw_rounded(surface, track, fill=pal.track, radius=4)
            draw_rounded(surface, pygame.Rect(track.x, track.y, max(4, int(track.w * job.progress)), track.h), fill=pal.accent, radius=4)
            blit_text(surface, self.theme.font(10), job.status, pal.text_muted, center=(card.centerx, card.y + 120))
            blit_text(surface, self.theme.font(9), "Copying one file at a time within the frame budget", pal.text_muted, center=(card.centerx, card.y + 142))

    def _draw_layer(self, surface: pygame.Surface, page: Page, offset: float, alpha: int) -> None:
        layer = self._layer
        layer.fill((0, 0, 0, 0))
        page.draw(layer, self.theme)
        page.draw_notes(layer, self.theme)
        layer.set_alpha(max(0, min(255, int(alpha))))
        surface.blit(layer, (int(round(offset)), 0))

    def _draw_header(self, surface: pygame.Surface, pal: "Palette") -> None:
        page = self._pages.get(self._current)
        title = page.title if page is not None else "Settings"
        subtitle = page.subtitle if page is not None else ""

        back = self._back_rect()
        pygame.draw.circle(surface, pal.surface, back.center, 22)
        pygame.draw.circle(surface, pal.stroke_soft, back.center, 22, 1)
        draw_chevron(
            surface,
            (back.centerx + 1, back.centery),
            pal.text,
            size=7,
            thickness=2,
            direction="left",
        )

        title_y = back.y - 4
        blit_text(
            surface,
            self.theme.font(24 if current_layout.compact else 28, True),
            title,
            pal.text,
            topleft=(back.right + 14, title_y),
        )
        if subtitle:
            blit_text(
                surface,
                self.theme.font(10),
                subtitle,
                pal.text_muted,
                topleft=(back.right + 16, back.bottom - 6),
                tracking=0.4,
            )

        line_y = back.bottom + (8 if current_layout.compact else 10)
        pygame.draw.line(
            surface,
            pal.stroke_soft,
            (current_layout.margin, line_y),
            (current_layout.width - current_layout.margin, line_y),
            1,
        )

    def _draw_hint(self, surface: pygame.Surface, pal: "Palette") -> None:
        if self._dialog is not None and self._dialog.is_open:
            return
        blit_text(
            surface,
            self.theme.font(10),
            "TAB / \u2191\u2193 navigate \u00b7 \u2190\u2192 adjust \u00b7 ENTER select \u00b7 ESC back",
            pal.text_muted,
            center=(current_layout.center_x, current_layout.height - 14),
            tracking=0.4,
        )
