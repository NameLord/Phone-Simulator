"""Phone OS simulator - boot entry point and the system-layer scenes.

Run with:  python main.py          (add --debug to start with the F1 overlay)
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Callable

import pygame

from core.display import PORTRAIT_SIZE
from core.input import EventKind, PointerEvent
from core.kernel import Kernel, KernelConfig
from core.scene import Scene, clamp, ease_in_out_cubic, ease_out_cubic, lerp, pulse
from ui.app_shell import AppScene
from ui.home_scene import HomeScene
from ui.lockscreen import LockScene
from ui.onboarding import OnboardingScene
from ui.paint import (
    blit_text,
    draw_home_indicator,
    draw_rounded,
    fit_text,
    make_glow,
    vertical_gradient,
)
from ui.layout import layout as current_layout
from ui.qa_scene import QAScene
from ui.settings_app import SettingsScene
from ui.spotlight import SpotlightScene
from ui.theme import SPLASH
from ui.widgets import ContextMenu

VIRTUAL = PORTRAIT_SIZE

# The boot splash keeps its brand palette; all of it lives in ui/theme.py.
INK = SPLASH.ink
DIM = SPLASH.dim
FAINT = SPLASH.faint
ACCENT = SPLASH.accent
ACCENT_2 = SPLASH.accent_2
SURFACE = SPLASH.logo_body
STROKE = SPLASH.stroke
BG_TOP = SPLASH.bg_top
BG_BOTTOM = SPLASH.bg_bottom


# ---------------------------------------------------------------------------
# shared drawing helpers live in ui/paint.py
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Ripple:
    """A tap feedback ring, either on a tile or on the lock screen."""

    x: float
    y: float
    born: float
    tile: int = -1
    life: float = 0.6


@dataclass(frozen=True, slots=True)
class HomeEntry:
    """One slot in the launcher grid."""

    kind: str
    app_id: str
    name: str


# ---------------------------------------------------------------------------
# boot
# ---------------------------------------------------------------------------
class BootScene(Scene):
    """Procedural logo + progress animation, then a fade into the lock screen."""

    name = "boot"
    shows_status_bar = False
    DURATION = 2.5

    def __init__(self, kernel: Kernel) -> None:
        super().__init__(kernel)
        self._background = vertical_gradient(self.size, BG_TOP, BG_BOTTOM)
        self._glow = make_glow(210, ACCENT, power=2.0)
        self._glow_big = pygame.transform.smoothscale(self._glow, (780, 780))
        self._logo_layer = pygame.Surface((320, 320), pygame.SRCALPHA)
        self._finished = False

    def on_enter(self) -> None:
        super().on_enter()
        device = self.registry.get("device")
        if device is not None and device.state.value in ("shutdown", "reboot"):
            device.begin_boot()
        elif device is not None and device.state.value == "boot":
            pass
        elif device is not None:
            device.begin_boot()

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self._background = vertical_gradient(self.size, BG_TOP, BG_BOTTOM)

    def update(self, dt: float) -> None:
        super().update(dt)
        # Navigation to the lock screen is owned by the kernel's device-state
        # listener, so boot taps/power presses cannot skip the init steps.

    def handle(self, event: PointerEvent) -> bool:
        return event.kind in (EventKind.TAP, EventKind.DOUBLE_TAP, EventKind.KEY_DOWN)

    def draw(self, surface: pygame.Surface) -> None:
        elapsed = self.elapsed
        width, height = surface.get_size()
        cx = width // 2
        logo_y = int(height * (0.33 if current_layout.is_landscape else 0.39))
        surface.blit(self._background, (0, 0))

        glow = self._glow_big
        glow.set_alpha(int(64 + 26 * pulse(elapsed, 2.6)))
        surface.blit(glow, glow.get_rect(center=(cx, logo_y)))

        appear = ease_out_cubic(clamp(elapsed / 0.65, 0.0, 1.0))
        scale = lerp(0.84, 1.0, appear)
        self._draw_mark(surface, cx, logo_y, scale, appear, elapsed)

        word_alpha = ease_out_cubic(clamp((elapsed - 0.42) / 0.75, 0.0, 1.0))
        if word_alpha > 0.01:
            blit_text(
                surface,
                self.font(30, True),
                "PHONE OS",
                INK,
                center=(cx, int(height * 0.545)),
                alpha=int(255 * word_alpha),
                tracking=11,
            )
            blit_text(
                surface,
                self.font(14),
                "VIRTUAL DEVICE RUNTIME",
                DIM,
                center=(cx, int(height * 0.578)),
                alpha=int(215 * word_alpha),
                tracking=5,
            )

        bar_alpha = clamp((elapsed - 0.95) / 0.4, 0.0, 1.0)
        device = self.registry.get("device")
        progress = device.boot_progress if device is not None else 0.0
        bar_width = max(120, min(232, width - 80))
        self._draw_progress(
            surface,
            cx,
            int(height * 0.67),
            bar_width,
            progress,
            int(255 * max(0.35, bar_alpha)),
        )
        if device is not None:
            step_font = self.mono(11)
            step_y = int(height * 0.67) + 38
            for index, step in enumerate(device.boot_steps):
                done = step.status == "done"
                active = step.status == "active"
                color = ACCENT if done or active else FAINT
                marker = "OK" if done else (">" if active else "..")
                blit_text(
                    surface,
                    step_font,
                    f"{marker}  {step.label}",
                    color,
                    center=(cx, step_y + index * 20),
                    alpha=255 if done or active else 150,
                    tracking=1.0,
                )

        footer_alpha = clamp((elapsed - 2.0) / 0.9, 0.0, 1.0)
        if footer_alpha > 0.01:
            blit_text(
                surface,
                self.font(12),
                "FOUNDATION BUILD",
                FAINT,
                center=(cx, height - 72),
                alpha=int(170 * footer_alpha),
                tracking=7,
            )

    def _draw_mark(
        self,
        surface: pygame.Surface,
        cx: float,
        cy: float,
        scale: float,
        alpha: float,
        elapsed: float,
    ) -> None:
        if alpha <= 0.004:
            return

        glow_size = max(8, int(420 * scale))
        glow = pygame.transform.smoothscale(self._glow, (glow_size, glow_size))
        glow.set_alpha(int(150 * alpha))
        surface.blit(glow, glow.get_rect(center=(int(cx), int(cy))))

        layer = self._logo_layer
        layer.fill((0, 0, 0, 0))

        body = pygame.Rect(0, 0, max(4, int(116 * scale)), max(4, int(196 * scale)))
        body.center = (160, 160)
        radius = max(4, int(28 * scale))
        pygame.draw.rect(layer, SURFACE, body, border_radius=radius)
        pygame.draw.rect(layer, ACCENT, body, width=max(2, int(3 * scale)), border_radius=radius)

        screen = body.inflate(-max(6, int(22 * scale)), -max(6, int(22 * scale)))
        pygame.draw.rect(layer, SPLASH.logo_screen, screen, border_radius=max(3, radius - 6))

        beat = 0.5 + 0.5 * math.sin(elapsed * 3.4)
        inner = screen.inflate(-int(screen.w * 0.36), -int(screen.h * 0.36))
        pygame.draw.rect(
            layer,
            (*ACCENT, int(50 + 110 * beat)),
            inner,
            border_radius=max(2, int(10 * scale)),
        )

        pygame.draw.rect(
            layer,
            (*ACCENT, 160),
            pygame.Rect(
                body.centerx - int(15 * scale),
                body.top + int(15 * scale),
                max(2, int(30 * scale)),
                max(2, int(4 * scale)),
            ),
            border_radius=2,
        )

        orbit = body.inflate(int(74 * scale), int(74 * scale))
        for index in range(3):
            start = elapsed * 1.7 + index * 2.094
            pygame.draw.arc(
                layer,
                (*ACCENT_2, 130),
                orbit,
                start,
                start + 1.05,
                max(1, int(2 * scale)),
            )

        layer.set_alpha(int(255 * alpha))
        surface.blit(layer, layer.get_rect(center=(int(cx), int(cy))))
        layer.set_alpha(255)

    def _draw_progress(
        self,
        surface: pygame.Surface,
        cx: int,
        cy: int,
        width: int,
        value: float,
        alpha: int,
    ) -> None:
        canvas = pygame.Surface((width + 40, 54), pygame.SRCALPHA)
        track = pygame.Rect(20, 8, width, 4)
        pygame.draw.rect(canvas, (*STROKE, alpha // 2), track, border_radius=2)
        if value > 0.0:
            filled = pygame.Rect(20, 8, max(2, int(width * value)), 4)
            pygame.draw.rect(canvas, (*ACCENT, alpha), filled, border_radius=2)
            pygame.draw.circle(canvas, (*ACCENT, alpha), (filled.right, 10), 4)
        pygame.draw.circle(canvas, (*STROKE, alpha // 2), (20, 10), 2)
        pygame.draw.circle(canvas, (*STROKE, alpha // 2), (20 + width, 10), 2)
        surface.blit(canvas, canvas.get_rect(center=(cx, cy)))

        label = "READY" if value >= 0.999 else f"{int(value * 100):02d}%"
        blit_text(surface, self.mono(13), label, DIM, center=(cx, cy + 22), alpha=alpha, tracking=2)


# ---------------------------------------------------------------------------
# home screen
# ---------------------------------------------------------------------------
class LegacyHomeScene(Scene):
    """Previous flat launcher, retained as a readable reference implementation."""

    name = "home"

    def __init__(self, kernel: Kernel) -> None:
        super().__init__(kernel)
        self._theme_key: tuple[object, ...] | None = None
        self._background = vertical_gradient(self.size, BG_TOP, BG_BOTTOM)
        self._glow = pygame.transform.smoothscale(make_glow(210, ACCENT, power=2.4), (760, 620))
        self._tiles: list[pygame.Rect] = []
        self._build_grid()
        self._press_anim: list[float] = [0.0] * len(self._tiles)
        self._active: int | None = None
        self._ripples: list[Ripple] = []
        self._page = 0
        self._menu: ContextMenu | None = None

    def _ensure_theme(self):
        """Rebuild cached gradients/glow when the live theme changes."""
        palette = self.theme.palette
        key = self.theme.signature
        if self._theme_key != key:
            self._theme_key = key
            self._background = vertical_gradient(self.size, palette.bg_top, palette.bg_bottom)
            glow = make_glow(210, palette.accent, power=2.4)
            self._glow = pygame.transform.smoothscale(glow, (760, 620))
        return palette

    def _build_grid(self) -> None:
        self._tiles = []
        margin = float(current_layout.margin)
        gap = 18.0 if current_layout.is_landscape else 26.0
        columns = 6 if current_layout.is_landscape else 4
        rows = 2 if current_layout.is_landscape else 5
        top = current_layout.status_bar_height + (18 if current_layout.is_landscape else 46)
        bottom = current_layout.height - 72
        tile_width = (self.size[0] - 2.0 * margin - gap * (columns - 1)) / columns
        tile_height = min(
            102.0 if not current_layout.is_landscape else 96.0,
            (bottom - top - gap * (rows - 1)) / rows,
        )
        row_pitch = tile_height + gap
        for row in range(rows):
            for column in range(columns):
                x = margin + column * (tile_width + gap)
                y = top + row * row_pitch
                self._tiles.append(
                    pygame.Rect(
                        int(round(x)),
                        int(round(y)),
                        int(round(tile_width)),
                        int(round(tile_height)),
                    )
                )
        self._press_anim = [0.0] * len(self._tiles)
        self._active = None

    def on_canvas_resize(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        self._background = vertical_gradient(self.size, self.theme.palette.bg_top, self.theme.palette.bg_bottom)
        self._theme_key = None
        self._build_grid()
        self._menu = None

    # -- launcher entries -----------------------------------------------------
    def _entries(self) -> list[HomeEntry]:
        manager = self.registry.require("app_manager")
        entries = [HomeEntry("settings", "settings", "Settings")]
        for manifest in manager.installed_manifests():
            entries.append(HomeEntry("app", manifest.app_id, manifest.name))
        return entries

    def _dock_entries(self) -> list[HomeEntry]:
        manager = self.registry.require("app_manager")
        return [
            HomeEntry("app", manifest.app_id, manifest.name)
            for manifest in manager.system_manifests()
        ]

    def _dock_rects(self) -> list[pygame.Rect]:
        entries = self._dock_entries()
        if not entries:
            return []
        size = 62 if current_layout.is_landscape else 68
        gap = 54
        total = len(entries) * size + (len(entries) - 1) * gap
        start = current_layout.center_x - total // 2
        y = current_layout.height - (112 if current_layout.is_landscape else 152)
        return [pygame.Rect(start + index * (size + gap), y, size, size) for index in range(len(entries))]

    def _page_count(self) -> int:
        return max(1, (len(self._entries()) + len(self._tiles) - 1) // len(self._tiles))

    def _entry_for_slot(self, slot: int) -> HomeEntry | None:
        index = self._page * len(self._tiles) + slot
        entries = self._entries()
        if 0 <= index < len(entries):
            return entries[index]
        return None

    def _dot_centers(self) -> list[tuple[int, int]]:
        count = self._page_count()
        if count <= 1:
            return []
        spacing = 18
        start = current_layout.center_x - (count - 1) * spacing / 2
        y = current_layout.height - 48
        return [(int(round(start + index * spacing)), y) for index in range(count)]

    def _dot_at(self, pos: tuple[float, float]) -> int | None:
        for index, center in enumerate(self._dot_centers()):
            if (pos[0] - center[0]) ** 2 + (pos[1] - center[1]) ** 2 <= 16 ** 2:
                return index
        return None

    def _set_page(self, page: int) -> None:
        count = self._page_count()
        self._page = max(0, min(count - 1, page))
        self._active = None
        self._press_anim = [0.0] * len(self._tiles)

    def _open_entry(self, entry: HomeEntry) -> None:
        if entry.kind == "settings":
            self.go("settings", fade=0.22)
            return
        manager = self.registry.require("app_manager")
        if manager.launch(entry.app_id) is not None:
            self.go("app", fade=0.16)

    def _open_menu(self, entry: HomeEntry, pos: tuple[float, float]) -> None:
        manager = self.registry.require("app_manager")

        def launch() -> None:
            self._open_entry(entry)

        def uninstall() -> None:
            if manager.uninstall(entry.app_id):
                self.notifications.post(
                    "system",
                    "App removed",
                    f"{entry.name} was uninstalled.",
                    self.theme.palette.warning,
                    banner=False,
                )
                self._page = min(self._page, self._page_count() - 1)

        if entry.kind == "settings":
            options: list[tuple[str, Callable[[], None] | None]] = [
                ("Open settings", launch),
                ("System app \u00b7 cannot uninstall", None),
            ]
        else:
            manifest = manager.manifest(entry.app_id)
            options = [("Open", launch)]
            if manifest is not None and not manifest.core and not manifest.system:
                options.append(("Uninstall", uninstall))
            else:
                options.append(("System app \u00b7 cannot uninstall", None))
        options.append(("Cancel", lambda: None))
        self._menu = ContextMenu(pygame.Rect(0, 0, *self.size), entry.name, options)
        self._menu.open(pos)

    def update(self, dt: float) -> None:
        super().update(dt)
        blend = min(1.0, dt * 16.0)
        for index, value in enumerate(self._press_anim):
            target = 1.0 if index == self._active else 0.0
            self._press_anim[index] = value + (target - value) * blend
        self._ripples = [ripple for ripple in self._ripples if self.elapsed - ripple.born < ripple.life]
        if self._menu is not None:
            self._menu.update(dt)

    def handle(self, event: PointerEvent) -> bool:
        if self._menu is not None and self._menu.is_open:
            self._menu.handle(event)
            return True

        if event.kind is EventKind.KEY_DOWN and event.key == pygame.K_ESCAPE:
            self.go("lock", fade=0.3)
            return True

        if event.kind is EventKind.POINTER_DOWN:
            self._active = self._hit(event.pos)
            return self._active is not None or self._dock_hit(event.pos) is not None

        if event.kind in (EventKind.TAP, EventKind.DOUBLE_TAP):
            page = self._dot_at(event.pos)
            if page is not None:
                self._set_page(page)
                return True
            docked = self._dock_hit(event.pos)
            if docked is not None:
                self._open_entry(self._dock_entries()[docked])
                return True
            index = self._hit(event.pos)
            if index is None:
                return False
            entry = self._entry_for_slot(index)
            if entry is None:
                return False
            self._add_ripple(event.pos, index)
            self._open_entry(entry)
            return True

        if event.kind is EventKind.LONG_PRESS:
            docked = self._dock_hit(event.pos)
            if docked is not None:
                self._open_menu(self._dock_entries()[docked], event.pos)
                return True
            index = self._hit(event.pos)
            if index is None:
                return False
            entry = self._entry_for_slot(index)
            if entry is None:
                return False
            self._add_ripple(event.pos, index, life=0.9)
            self._open_menu(entry, event.pos)
            return True

        if event.kind in (
            EventKind.POINTER_UP,
            EventKind.DRAG_START,
            EventKind.DRAG_END,
            EventKind.SWIPE,
        ):
            self._active = None
            return False

        return False

    def _hit(self, pos: tuple[float, float]) -> int | None:
        for index, rect in enumerate(self._tiles):
            if rect.collidepoint(pos):
                return index
        return None

    def _dock_hit(self, pos: tuple[float, float]) -> int | None:
        for index, rect in enumerate(self._dock_rects()):
            if rect.collidepoint(pos):
                return index
        return None

    def _add_ripple(self, pos: tuple[float, float], index: int, life: float = 0.6) -> None:
        self._ripples.append(Ripple(pos[0], pos[1], self.elapsed, tile=index, life=life))

    def draw(self, surface: pygame.Surface) -> None:
        palette = self._ensure_theme()
        elapsed = self.elapsed
        fx = self.registry.get("fx")
        if fx is not None:
            fx.draw_wallpaper(surface)
        else:
            surface.blit(self._background, (0, 0))

        glow = self._glow
        glow.set_alpha(int(38 + int(16 * pulse(elapsed, 4.2))))
        surface.blit(
            glow,
            glow.get_rect(center=(current_layout.center_x, current_layout.status_bar_height + 84)),
        )

        self._page = min(self._page, self._page_count() - 1)
        for index, rect in enumerate(self._tiles):
            self._draw_tile(surface, rect, index, palette)

        self._draw_dock(surface, palette)
        self._draw_page_dots(surface, palette)
        draw_home_indicator(surface, palette.home_indicator)
        if self._menu is not None:
            self._menu.draw(surface, self.theme)

    def _draw_dock(self, surface: pygame.Surface, palette) -> None:
        entries = self._dock_entries()
        rects = self._dock_rects()
        if not entries:
            return
        manager = self.registry.require("app_manager")
        panel = pygame.Rect(0, 0, 0, 0)
        if rects:
            panel = pygame.Rect(rects[0].x - 20, rects[0].y - 12, rects[-1].right - rects[0].x + 40, rects[0].h + 24)
            draw_rounded(surface, panel, fill=palette.surface_alt, radius=26, alpha=120)
        for entry, rect in zip(entries, rects):
            manifest = manager.manifest(entry.app_id)
            if manifest is not None:
                manifest.icon(surface, rect, self.theme)

    def _draw_tile(self, surface: pygame.Surface, rect: pygame.Rect, slot: int, palette) -> None:
        entry = self._entry_for_slot(slot)
        if entry is None:
            return

        anim = self._press_anim[slot]
        scale = 1.0 - 0.05 * anim
        size = max(24, int(min(62, rect.h * 0.58) * scale))
        icon_rect = pygame.Rect(0, 0, size, size)
        icon_rect.midtop = (rect.centerx, rect.y + max(4, int(rect.h * 0.08)))

        if anim > 0.01:
            halo = pygame.Surface((icon_rect.w + 28, icon_rect.h + 28), pygame.SRCALPHA)
            pygame.draw.rect(
                halo,
                (*palette.accent, int(48 * anim)),
                halo.get_rect(),
                border_radius=26,
            )
            surface.blit(halo, halo.get_rect(center=icon_rect.center))

        if entry.kind == "settings":
            draw_rounded(
                surface,
                icon_rect,
                fill=palette.surface_raised,
                stroke=palette.stroke,
                radius=16,
            )
            rows = (-11, 0, 11)
            knobs = (-9, 7, -3)
            for offset, knob in zip(rows, knobs):
                y = icon_rect.centery + offset
                pygame.draw.line(
                    surface,
                    palette.text_muted,
                    (icon_rect.x + 14, y),
                    (icon_rect.right - 14, y),
                    2,
                )
                pygame.draw.circle(
                    surface,
                    palette.accent_ink,
                    (icon_rect.centerx + knob, y),
                    4,
                )
        else:
            manifest = self.registry.require("app_manager").manifest(entry.app_id)
            if manifest is not None:
                manifest.icon(surface, icon_rect, self.theme)

        label_font = self.font(11)
        blit_text(
            surface,
            label_font,
            fit_text(label_font, entry.name, rect.w + 6),
            palette.text_secondary,
            center=(rect.centerx, rect.bottom - 16),
        )

        for ripple in self._ripples:
            if ripple.tile != slot:
                continue
            progress = (self.elapsed - ripple.born) / ripple.life
            if progress >= 1.0:
                continue
            ring_radius = int(8 + 64 * ease_out_cubic(progress))
            alpha = int(140 * (1.0 - progress))
            layer = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
            pygame.draw.circle(
                layer,
                (*palette.accent, alpha),
                (int(ripple.x - rect.x), int(ripple.y - rect.y)),
                ring_radius,
                2,
            )
            surface.blit(layer, rect.topleft)

    def _draw_page_dots(self, surface: pygame.Surface, palette) -> None:
        for index, center in enumerate(self._dot_centers()):
            color = palette.text if index == self._page else palette.text_muted
            pygame.draw.circle(surface, color, center, 3)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def build_kernel() -> Kernel:
    config = KernelConfig(
        title="Phone Simulator",
        virtual_size=VIRTUAL,
        target_fps=60,
        debug_overlay="--debug" in sys.argv,
    )
    kernel = Kernel(config)
    kernel.register_scene(BootScene.name, BootScene)
    kernel.register_scene(LockScene.name, LockScene)
    kernel.register_scene(OnboardingScene.name, OnboardingScene)
    kernel.register_scene(SpotlightScene.name, SpotlightScene)
    kernel.register_scene(HomeScene.name, HomeScene)
    kernel.register_scene(SettingsScene.name, SettingsScene)
    kernel.register_scene(AppScene.name, AppScene)
    kernel.register_scene(QAScene.name, QAScene)
    _seed_notifications(kernel)
    return kernel


def _seed_notifications(kernel: Kernel) -> None:
    """Give the shade real content on first boot."""
    palette = kernel.theme.palette
    kernel.notifications.post(
        "system",
        "Welcome to PhoneOS",
        "System layer online: lock screen, status bar, settings and shade.",
        palette.accent,
        banner=False,
    )
    kernel.notifications.post(
        "battery",
        f"Battery at {kernel.device_clock.battery_level}%",
        "Simulated drain is running. Power saver halves the rate.",
        palette.success,
        banner=False,
    )
    kernel.notifications.post(
        "settings",
        "Try the Settings tile",
        "Tap the gear on the home screen, or drag down from the top edge.",
        palette.warning,
        read=True,
        banner=False,
    )


def main() -> int:
    kernel = build_kernel()
    kernel.run(BootScene.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
