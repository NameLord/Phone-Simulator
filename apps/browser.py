"""Browser: a self-contained fake web with a hardcoded page graph.

No network, no external assets. Pages, links and URLs are defined in code;
history is an in-memory back/forward stack. Body text wraps to the viewport and
scrolls with the arrow keys when it overflows.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

from apps.base_app import App, AppContext
from core.input import EventKind, PointerEvent
from ui.paint import blit_text, draw_chevron, draw_rounded, fit_text, wrap_text

APP_ID = "browser"
APP_NAME = "Browser"


@dataclass(frozen=True, slots=True)
class Page:
    title: str
    url: str
    body: tuple[str, ...]
    links: tuple[tuple[str, str], ...] = ()


PAGES: dict[str, Page] = {
    "home": Page(
        title="NovaNet",
        url="https://nova.net/",
        body=(
            "A tiny, entirely fake network running inside the simulator. Every page is "
            "generated from a dictionary in the browser app.",
            "Nothing here touches the internet. Links are buttons with a URL attached, "
            "and the back/forward history lives in memory only.",
        ),
        links=(
            ("Foundation release notes", "news"),
            ("Browser documentation", "docs"),
            ("Nova Store", "shop"),
            ("About this device", "about"),
        ),
    ),
    "news": Page(
        title="Release notes",
        url="https://nova.net/news/foundation",
        body=(
            "PhoneOS 2.0 \u2014 foundation build.",
            "The system layer now ships with persisted settings, a live theme, a "
            "notification shade, a PIN lock screen and a complete Settings app.",
            "This build adds the app platform: a launcher grid, an app store, Recents, "
            "and working Calculator, Notes, Music and Browser apps.",
        ),
        links=(
            ("Back to NovaNet", "home"),
            ("How the Browser works", "docs"),
        ),
    ),
    "docs": Page(
        title="Browser help",
        url="https://nova.net/docs/browser",
        body=(
            "Tap a link to navigate. Use the arrows beside the address bar to move "
            "through history; forward is discarded when you branch off.",
            "The body scrolls with Up/Down or PageUp/PageDown when a page is taller "
            "than the viewport. Backspace also goes back.",
            "URLs are cosmetic. There is no DNS, no sockets and no HTML engine.",
        ),
        links=(
            ("NovaNet home", "home"),
            ("Release notes", "news"),
        ),
    ),
    "shop": Page(
        title="Nova Store",
        url="https://nova.net/store",
        body=(
            "The Music app is available as an installable title in the App Store. "
            "Open the Store from the home screen, install it, and a new icon appears.",
            "Core apps such as the Store itself cannot be removed.",
        ),
        links=(
            ("NovaNet home", "home"),
            ("About this device", "about"),
        ),
    ),
    "about": Page(
        title="About",
        url="https://nova.net/about",
        body=(
            "Device: Nova One (virtual). The logical canvas rotates between portrait and landscape.",
            "Everything you see is drawn procedurally with pygame, including this page.",
        ),
        links=(("NovaNet home", "home"),),
    ),
}


def draw_icon(surface: pygame.Surface, rect: pygame.Rect, theme) -> None:
    pal = theme.palette
    draw_rounded(surface, rect, fill=pal.surface_raised, stroke=pal.stroke, radius=max(8, rect.w // 4))
    center = rect.center
    radius = int(rect.w * 0.28)
    pygame.draw.circle(surface, pal.accent, center, radius, 2)
    pygame.draw.ellipse(
        surface,
        pal.accent,
        pygame.Rect(center[0] - radius // 2, center[1] - radius, radius, radius * 2),
        2,
    )
    pygame.draw.line(
        surface,
        pal.accent,
        (center[0] - radius, center[1]),
        (center[0] + radius, center[1]),
        2,
    )


class BrowserApp(App):
    app_id = APP_ID
    name = APP_NAME

    def __init__(self, context: AppContext) -> None:
        super().__init__(context)
        self._history: list[str] = ["home"]
        self._index = 0
        self._scroll = 0.0
        self._link_rects: list[tuple[pygame.Rect, str]] = []
        self._content_height = 0.0
        self._page_id = "home"

    # -- navigation -----------------------------------------------------------
    @property
    def page(self) -> Page:
        return PAGES.get(self._page_id, PAGES["home"])

    @property
    def can_back(self) -> bool:
        return self._index > 0

    @property
    def can_forward(self) -> bool:
        return self._index < len(self._history) - 1

    @property
    def online(self) -> bool:
        return self.network.internet if self.network is not None else True

    def navigate(self, page_id: str) -> None:
        if not self.online:
            self._scroll = 0.0
            return
        if page_id not in PAGES or page_id == self._page_id:
            return
        self._history = self._history[: self._index + 1]
        self._history.append(page_id)
        self._index = len(self._history) - 1
        self._page_id = page_id
        self._scroll = 0.0

    def go_back(self) -> None:
        if self.can_back:
            self._index -= 1
            self._page_id = self._history[self._index]
            self._scroll = 0.0

    def go_forward(self) -> None:
        if self.can_forward:
            self._index += 1
            self._page_id = self._history[self._index]
            self._scroll = 0.0

    # -- input ----------------------------------------------------------------
    def handle(self, event: PointerEvent) -> bool:
        if event.kind is EventKind.TAP:
            if self._back_rect().collidepoint(event.pos):
                self.go_back()
                return True
            if self._forward_rect().collidepoint(event.pos):
                self.go_forward()
                return True
            if self._home_rect().collidepoint(event.pos):
                self.navigate("home")
                return True
            if not self.online:
                return True
            for rect, target in self._link_rects:
                if rect.collidepoint(event.pos):
                    self.navigate(target)
                    return True
            return False
        if event.kind is EventKind.KEY_DOWN:
            if event.key in (pygame.K_BACKSPACE, pygame.K_LEFT):
                self.go_back()
                return True
            if event.key == pygame.K_RIGHT:
                self.go_forward()
                return True
            if event.key == pygame.K_DOWN:
                self._scroll = min(self._max_scroll(), self._scroll + 44.0)
                return True
            if event.key == pygame.K_UP:
                self._scroll = max(0.0, self._scroll - 44.0)
                return True
            if event.key == pygame.K_PAGEDOWN:
                self._scroll = min(self._max_scroll(), self._scroll + 360.0)
                return True
            if event.key == pygame.K_PAGEUP:
                self._scroll = max(0.0, self._scroll - 360.0)
                return True
            if (event.text or "").lower() == "h":
                self.navigate("home")
                return True
            return False
        return False

    # -- rects ----------------------------------------------------------------
    def _address_rect(self) -> pygame.Rect:
        return pygame.Rect(20, self.content.y + 12, self.content.w - 40, 46)

    def _back_rect(self) -> pygame.Rect:
        return pygame.Rect(20, self._address_rect().bottom + 10, 44, 40)

    def _forward_rect(self) -> pygame.Rect:
        return pygame.Rect(72, self._address_rect().bottom + 10, 44, 40)

    def _home_rect(self) -> pygame.Rect:
        return pygame.Rect(124, self._address_rect().bottom + 10, 100, 40)

    def _view_rect(self) -> pygame.Rect:
        top = self._home_rect().bottom + 10
        return pygame.Rect(20, top, self.content.w - 40, max(80, self.content.bottom - 32 - top))

    def _max_scroll(self) -> float:
        view = self._view_rect()
        return max(0.0, self._content_height - (view.h - 28))

    # -- drawing --------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        pal = self.palette
        surface.fill(pal.bg_bottom)

        address = self._address_rect()
        draw_rounded(surface, address, fill=pal.surface, stroke=pal.stroke_soft, radius=16)
        pygame.draw.circle(surface, pal.success, (address.x + 22, address.centery), 4)
        url_font = self.mono(13)
        blit_text(
            surface,
            url_font,
            fit_text(url_font, self.page.url, address.w - 60),
            pal.text_secondary,
            midleft=(address.x + 36, address.centery),
        )

        self._draw_nav_button(surface, self._back_rect(), "\u2039", self.can_back)
        self._draw_nav_button(surface, self._forward_rect(), "\u203a", self.can_forward)
        home = self._home_rect()
        draw_rounded(surface, home, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, self.font(11, True), "NOVA.NET", pal.text_secondary, center=home.center, tracking=1.2)

        view = self._view_rect()
        if not self.online:
            draw_rounded(surface, view, fill=pal.surface, stroke=pal.stroke_soft, radius=22)
            blit_text(surface, self.font(24, True), "No internet connection", pal.text, center=(view.centerx, view.centery - 30))
            blit_text(surface, self.font(13), "Turn on Wi-Fi in Quick Settings and leave Airplane Mode off.", pal.text_secondary, center=(view.centerx, view.centery + 4))
            blit_text(surface, self.font(11), "Browser pages are unavailable offline.", pal.text_muted, center=(view.centerx, view.centery + 30))
            return
        draw_rounded(surface, view, fill=pal.surface, stroke=pal.stroke_soft, radius=22)

        previous_clip = surface.get_clip()
        surface.set_clip(view.inflate(-2, -2))
        self._link_rects = []
        y = view.y + 22 - int(self._scroll)

        title_font = self.font(25, True)
        blit_text(surface, title_font, self.page.title, pal.text, topleft=(view.x + 22, y))
        y += 44

        body_font = self.font(14)
        line_height = 21
        for paragraph in self.page.body:
            for line in wrap_text(body_font, paragraph, view.w - 44):
                blit_text(surface, body_font, line, pal.text_secondary, topleft=(view.x + 22, y))
                y += line_height
            y += 10

        y += 6
        for label, target in self.page.links:
            rect = pygame.Rect(view.x + 18, y, view.w - 36, 46)
            self._link_rects.append((rect, target))
            draw_rounded(surface, rect, fill=pal.surface_alt, stroke=pal.stroke_soft, radius=12)
            link_font = self.font(14, True)
            blit_text(
                surface,
                link_font,
                fit_text(link_font, label, rect.w - 56),
                pal.accent_ink,
                midleft=(rect.x + 16, rect.centery),
            )
            blit_text(
                surface,
                self.font(10),
                PAGES[target].url.replace("https://", ""),
                pal.text_muted,
                midright=(rect.right - 34, rect.centery),
            )
            draw_chevron(surface, (rect.right - 18, rect.centery), pal.text_muted, size=5)
            y += 56

        self._content_height = y - (view.y - int(self._scroll)) + 18
        surface.set_clip(previous_clip)

        max_scroll = self._max_scroll()
        if max_scroll > 0.0:
            self._scroll = max(0.0, min(self._scroll, max_scroll))
            track = pygame.Rect(view.right - 7, view.y + 14, 3, view.h - 28)
            pygame.draw.rect(surface, pal.track, track, border_radius=2)
            thumb_height = max(30, int(track.h * (view.h / max(1.0, self._content_height))))
            progress = self._scroll / max_scroll
            thumb_y = track.y + int((track.h - thumb_height) * progress)
            pygame.draw.rect(surface, pal.accent, pygame.Rect(track.x, thumb_y, track.w, thumb_height), border_radius=2)
            blit_text(
                surface,
                self.font(10),
                "\u2191\u2193 scroll",
                pal.text_muted,
                midright=(view.right - 18, view.bottom - 16),
            )

        blit_text(
            surface,
            self.font(10),
            f"history {self._index + 1}/{len(self._history)} \u00b7 Backspace back \u00b7 H home",
            pal.text_muted,
            center=(self.content.centerx, self.content.bottom - 12),
        )

    def _draw_nav_button(self, surface: pygame.Surface, rect: pygame.Rect, glyph: str, enabled: bool) -> None:
        pal = self.palette
        fill = pal.surface_raised if enabled else pal.surface_alt
        color = pal.text if enabled else pal.text_muted
        draw_rounded(surface, rect, fill=fill, stroke=pal.stroke_soft, radius=14)
        blit_text(surface, self.font(24, True), glyph, color, center=(rect.centerx, rect.centery - 1))

    # -- icon -----------------------------------------------------------------
    def icon_draw(self, surface: pygame.Surface, rect: pygame.Rect) -> None:
        draw_icon(surface, rect, self.theme)
