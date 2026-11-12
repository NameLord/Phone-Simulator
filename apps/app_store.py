"""App Store: simulated catalog, 2-second install progress and uninstall.

The catalog comes from the app manager's manifests. Installing appends to the
persisted ``apps.installed`` list; the home screen reads it live. Core apps are
flagged in their manifest and cannot be removed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pygame

from apps._kit import ScrollState, draw_scrollbar
from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.layout import layout as current_layout
from ui.paint import blit_text, draw_rounded, fit_text, with_alpha, wrap_text

if TYPE_CHECKING:
    from services.app_manager import AppManifest

APP_ID = "store"
APP_NAME = "App Store"
INSTALL_SECONDS = 2.0


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    bag = rect.inflate(-int(rect.w * 0.5), -int(rect.h * 0.46))
    bag.centery += int(rect.h * 0.08)
    pygame.draw.rect(surface, pal.accent, bag, 2, border_radius=4)
    pygame.draw.arc(
        surface,
        pal.accent,
        pygame.Rect(bag.centerx - bag.w // 3, bag.y - 8, (bag.w // 3) * 2, 16),
        0,
        3.14159,
        2,
    )
    pygame.draw.circle(surface, pal.accent, bag.center, 3)


class AppStoreApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._installing_id: str | None = None
        self._progress = 0.0
        self._flash: str | None = None
        self._flash_t = 0.0
        self._row_rects: list[pygame.Rect] = []
        self._button_rects: list[pygame.Rect] = []
        self._scroll = ScrollState(step=72)

    # -- layout ---------------------------------------------------------------
    def _columns(self) -> int:
        if current_layout.is_landscape:
            return 3 if self.content.w >= 960 else 2
        return 2 if self.content.w >= 760 else 1

    def _row_rect(self, index: int) -> pygame.Rect:
        count = len(self._catalog())
        columns = self._columns()
        rows = max(1, (count + columns - 1) // columns)
        margin = 20
        gap = 10
        top = self.content.y + 48
        available = self.content.bottom - 44 - top
        height = max(64, (available - gap * (rows - 1)) // rows)
        width = (self.content.w - margin * 2 - gap * (columns - 1)) // columns
        row, column = divmod(index, columns)
        return pygame.Rect(
            margin + column * (width + gap),
            top + row * (height + gap) - int(self._scroll.offset),
            width,
            min(height, 142),
        )

    def _button_rect(self, index: int) -> pygame.Rect:
        row = self._row_rect(index)
        width = 96 if row.w < 460 else 108
        return pygame.Rect(row.right - width - 16, row.y + 14, width, 36)

    # -- state ----------------------------------------------------------------
    def _catalog(self) -> tuple["AppManifest", ...]:
        return tuple(
            manifest for manifest in self.ctx.apps.catalog() if not getattr(manifest, "system", False)
        )

    def _start_install(self, manifest: "AppManifest") -> None:
        if self.ctx.network is not None and not self.ctx.network.internet:
            self.notify("No internet", "Turn on Wi-Fi in Quick Settings to download apps.")
            return
        if self._installing_id is not None or self.ctx.apps.is_installed(manifest.app_id):
            return
        self._installing_id = manifest.app_id
        self._progress = 0.0
        self._flash = manifest.app_id
        self._flash_t = 1.2

    def _uninstall(self, manifest: "AppManifest") -> None:
        if manifest.core:
            return
        if self.ctx.apps.uninstall(manifest.app_id):
            self.notify("App removed", f"{manifest.name} was uninstalled.")
            self._flash = manifest.app_id
            self._flash_t = 1.2

    def update(self, dt: float) -> None:
        super().update(dt)
        if self._flash_t > 0.0:
            self._flash_t = max(0.0, self._flash_t - dt)
            if self._flash_t == 0.0:
                self._flash = None

        if self._installing_id is None:
            return
        self._progress += dt
        if self._progress < INSTALL_SECONDS:
            return
        manifest = self.ctx.apps.manifest(self._installing_id)
        installed = self.ctx.apps.install(self._installing_id) if manifest is not None else False
        if installed and manifest is not None:
            self.notify("App installed", f"{manifest.name} is now on your home screen.")
        self._installing_id = None
        self._progress = 0.0

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        area = self._scroll_area()
        if event.kind is EventKind.WHEEL:
            return self._scroll.wheel(event)
        if event.kind is EventKind.POINTER_DOWN:
            self._scroll.pointer(event, area)
        if event.kind in (EventKind.DRAG, EventKind.DRAG_END, EventKind.POINTER_UP):
            self._scroll.pointer(event, area)
        if event.kind is EventKind.KEY_DOWN:
            if self._scroll.key(event):
                return True
        if event.kind is not EventKind.TAP:
            return event.kind is EventKind.POINTER_DOWN
        catalog = self._catalog()
        for index, manifest in enumerate(catalog):
            if self._button_rect(index).collidepoint(event.pos):
                if self._installing_id is not None:
                    return True
                if not self.ctx.apps.is_installed(manifest.app_id):
                    self._start_install(manifest)
                elif not manifest.core:
                    self._uninstall(manifest)
                return True
        return False

    # -- drawing --------------------------------------------------------------
    def _scroll_area(self) -> pygame.Rect:
        return pygame.Rect(20, self.content.y + 48, self.content.w - 40, max(80, self.content.bottom - 88 - (self.content.y + 48)))

    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)
        catalog = self._catalog()
        columns = self._columns()
        rows = max(1, (len(catalog) + columns - 1) // columns)
        area = self._scroll_area()
        row_height = min(142, max(64, (area.h - 10 * (rows - 1)) // rows))
        self._scroll.set_metrics(area.h, rows * (row_height + 10) - 10)

        blit_text(
            surface,
            self.font(11),
            "FEATURED \u00b7 SIMULATED CATALOG",
            pal.text_muted,
            topleft=(22, self.content.y + 18),
            tracking=2.0,
        )
        self._row_rects = []
        self._button_rects = []
        catalog = self._catalog()
        surface.set_clip(area)
        for index, manifest in enumerate(catalog):
            self._draw_row(surface, index, manifest)
        surface.set_clip(None)

        blit_text(
            surface,
            self.font(10),
            "Core apps cannot be removed \u00b7 installs finish in 2s",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 16),
        )
        draw_scrollbar(surface, area, self._scroll.offset, rows * (row_height + 10) - 10, pal)
        if self._installing_id is not None:
            name = self.ctx.apps.manifest(self._installing_id)
            blit_text(
                surface,
                self.font(11),
                f"Installing {name.name if name else 'app'}\u2026",
                pal.accent_ink,
                center=(self.content.centerx, self.content.bottom - 34),
            )

    def _draw_row(self, surface: pygame.Surface, index: int, manifest: "AppManifest") -> None:
        pal = self.palette
        row = self._row_rect(index)
        self._row_rects.append(row)
        draw_rounded(surface, row, fill=pal.surface, stroke=pal.stroke_soft, radius=20)

        icon_size = max(40, min(64, row.h - 38))
        icon_rect = pygame.Rect(row.x + 16, row.y + 14, icon_size, icon_size)
        manifest.icon(surface, icon_rect, self.theme)

        text_x = icon_rect.right + 14
        text_width = row.right - text_x - 126
        name_font = self.font(15, True)
        blit_text(
            surface,
            name_font,
            fit_text(name_font, manifest.name, text_width),
            pal.text,
            topleft=(text_x, row.y + 12),
        )
        blit_text(
            surface,
            self.font(9),
            manifest.category.upper(),
            pal.text_muted,
            topleft=(text_x, row.y + 34),
            tracking=1.0,
        )

        desc_font = self.font(10)
        lines = wrap_text(desc_font, manifest.description, text_width)[: 2 if row.h >= 100 else 1]
        y = row.y + 52
        for line in lines:
            blit_text(surface, desc_font, line, pal.text_secondary, topleft=(text_x, y))
            y += 15

        button = self._button_rect(index)
        self._button_rects.append(button)
        installed = self.ctx.apps.is_installed(manifest.app_id)
        installing = self._installing_id == manifest.app_id

        if installing:
            fraction = min(1.0, self._progress / INSTALL_SECONDS)
            draw_rounded(surface, button, fill=pal.surface_alt, stroke=pal.accent, radius=12)
            blit_text(
                surface,
                self.font(11, True),
                f"{int(fraction * 100)}%",
                pal.accent_ink,
                center=button.center,
            )
            bar = pygame.Rect(text_x, row.bottom - 24, row.w - (text_x - row.x) - 20, 5)
            pygame.draw.rect(surface, pal.track, bar, border_radius=2)
            pygame.draw.rect(
                surface,
                pal.accent,
                pygame.Rect(bar.x, bar.y, max(2, int(bar.w * fraction)), bar.h),
                border_radius=2,
            )
        elif manifest.core:
            draw_rounded(surface, button, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=12)
            blit_text(surface, self.font(11, True), "CORE", pal.text_muted, center=button.center, tracking=1.4)
        elif installed:
            draw_rounded(surface, button, fill=pal.surface_alt, stroke=pal.danger, radius=12)
            blit_text(surface, self.font(11, True), "REMOVE", pal.danger, center=button.center, tracking=1.0)
        else:
            draw_rounded(surface, button, fill=pal.accent, radius=12)
            blit_text(surface, self.font(12, True), "INSTALL", pal.on_accent, center=button.center, tracking=1.0)

        if installed and not manifest.core and not installing:
            blit_text(
                surface,
                self.font(9),
                "installed",
                pal.text_muted,
                midright=(button.right, button.bottom + 14),
            )

        if self._flash == manifest.app_id and self._flash_t > 0.0:
            glow = pygame.Surface(row.size, pygame.SRCALPHA)
            pygame.draw.rect(glow, with_alpha(pal.accent, int(28 * self._flash_t)), glow.get_rect(), border_radius=20)
            surface.blit(glow, row.topleft)

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
